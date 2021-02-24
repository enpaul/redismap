"""Wrapper for Redis servers to support using a server as a python mapping"""
import datetime
import json
from typing import Any
from typing import Dict
from typing import Generator
from typing import Iterable
from typing import List
from typing import MutableMapping
from typing import Optional
from typing import Tuple
from typing import Union

import redis


__title__ = "redismap"
__summary__ = "A Redis wrapper that lets Redis be used as a python mapping"
__version__ = "0.1.0"
__url__ = "https://github.com/enpaul/redismap/"
__license__ = "MIT"
__authors__ = ["Ethan Paul <24588726+enpaul@users.noreply.github.com>"]


JSONBaseType = Optional[Union[int, float, bool, List, Dict]]

JSONValue = Union[List[JSONBaseType], Dict[str, JSONBaseType]]

RedisValue = Union[str, int, float, JSONValue]


class RedisMapException(Exception):
    """Base error with the Redis mapping"""


class ConnectionUninitializedError(RedisMapException):
    """Redis connection has not been initialized"""


class ConnectionAlreadyInitializedError(RedisMapException):
    """Redis connection cannot be initialized after already opened"""


class DatabaseUnreachableError(RedisMapException):
    """Failed to open connection to the Redis database"""


class RedisMap(MutableMapping):
    """Wrapper class for the ``redis.Redis`` database connection interface

    This class behaves like a traditional dictionary, and can be used in the same way. It implements
    all the methods expected of a mapping, however several concessions have been made in the
    interest of simplicity and performance:

    * This class stores all data using Redis's ``string`` type (i.e. the ``SET`` command). To
      support complex data structures like lists and dictionaries, all data is JSON-serialized
      before being sent to the database. This has two primary implications: first, all data assigned
      to a key of this class must be JSON-serializable. Second, the functionality of native Redis
      datatypes for sets, lists, and hashes is not supported.
    * Only a single key expiration time can be set for each instance of this class. This means that
      all keys set on that instance will be assigned that expiration time.
    * Race conditions are possible when using the :meth:`values`, :meth:`items`, and :meth:`update`
      methods. For this reason it is recommended that all read and write operations interacting with
      an instance of this class are wrapped in a transaction.

    These concessions are not insignificant and result in this wrapper class implementing a very
    small subset of the functionality provided by an instance of :class:`redis.Redis`. For that
    reason the underlying database connection class is exposed under the ``database`` property.

    .. note:: While the :class:`redis.Redis` class will allow keys to be of type ``bytes``, ``int``,
              or ``float`` (converting them to strings when sending them to the database) this class
              requires that keys be of type ``str`` only.

    :property database: Redis connection object used for accessing the database. Can be used to
                        make calls directly against the Redis API.
    :property namespace: Key namespace applied to keys set by the cache wrapper.
    :property expiration: Expiration applied to keys set by the cache wrapper.
    """

    def __init__(
        self,
        *args,
        expiration: Optional[datetime.timedelta] = None,
        namespace: Optional[str] = None,
        **kwargs,
    ):
        """Initialize the cache wrapper

        .. note:: If neither ``args`` nor ``kwargs`` are provided then the database connection is
                  left unititialized. The :meth:`initialize` method can be used to open the database
                  connection after the wrapper is created.

        :param args: Arguments to pass into the :class:`redis.Redis` constructor
        :param kwargs: Keyword arguments to pass into the :class:`redis.Redis` constructor
        :param expiration: Timedelta indicating how long keys set by the wrapper should persist in
                           the Redis schema before expiring. Applies to all keys set by the wrapper.
        :param namespace: Key namespace applied to keys set by the wrapper. Assigning different
                          namespaces to different wrappers allows multiple wrappers to share a
                          single Redis schema.
        """

        self._namespace: Optional[str] = namespace
        self._expiration: Optional[datetime.timedelta] = expiration
        self._database: Optional[redis.Redis]
        if args or kwargs:
            self._database = redis.Redis(*args, **kwargs)
        else:
            self._database = None

    def __len__(self) -> int:
        return len(self.keys())

    def __getitem__(self, key: str) -> RedisValue:
        self._check_connection()
        value = self._database.get(self._expand_key(key))
        try:
            if value is not None:
                return json.loads(value)
            raise KeyError(f"No key '{key}' in the Redis schema")
        except json.JSONDecodeError as err:
            raise ValueError(f"Invalid JSON in redis schema: {err}")

    def __setitem__(self, key: str, value: RedisValue) -> None:
        self._check_connection()
        try:
            self._database.set(
                self._expand_key(key), json.dumps(value), ex=self.expiration_seconds
            )
        except json.JSONDecodeError as err:
            raise ValueError(
                "Value for specified key is not JSON-serializable"
            ) from err

    def __delitem__(self, key: str) -> None:
        self._check_connection()
        full_key = self._expand_key(key)
        if self._database.exists(full_key) != 1:
            raise KeyError(f"No key '{key}' in the Redis schema")
        self._database.delete(full_key)

    def __iter__(self) -> Iterable[str]:
        return iter(self.keys())

    def __contains__(self, key: str) -> bool:
        self._check_connection()
        return self._database.exists(self._expand_key(key)) == 1

    def _expand_key(self, key: str) -> str:
        """Expand a key to its full name, performing common checks

        .. note:: This method will also call :meth:`_check_connection`.

        :raises TypeError: If the provided key is not a string
        :returns: The full key name, with pre-pended namespace delimiter if one has been specified
                  for the wrapper
        """
        if not isinstance(key, str):
            raise TypeError(f"Redis keys must be of type 'str', got '{type(key)}'")
        return f"{self._namespace}:{key}" if self._namespace else key

    def _check_connection(self):
        """Checks that the redis connection is configured and accessible

        :raises exceptions.DatabaseUninitializedError: If no database connection details have been
                                                       provided to the wrapper class.
        :raises exceptions.DatabaseUnreachableError: If the database cannot be reached.
        """
        if self._database is None:
            raise ConnectionUninitializedError(
                "Connection to redis database has not been created"
            )
        if not self._database.ping():
            raise DatabaseUnreachableError("Redis database is unreachable")

    @property
    def database(self) -> Optional[redis.Redis]:
        """Return the underlying database connection object

        Intended for usage with more advanced database features that the wrapper does not implement.

        .. note:: Will return ``None`` if the database connection has not been initialized.
        """
        return self._database

    @property
    def namespace(self) -> Optional[str]:
        """Returns the optional key namespace prefix applied to keys set by the wrapper"""
        return self._namespace

    @property
    def expiration(self) -> Optional[datetime.timedelta]:
        """Returns the optional expiration time applied to keys set by the wrapper"""
        return self._expiration

    @property
    def expiration_seconds(self) -> Optional[float]:
        return (
            self._expiration.total_seconds() if self._expiration is not None else None
        )

    def initialize(self, *args, **kwargs):
        """Initialize the connection to the database after the wrapper is created

        :param args: Arguments to pass into the :class:`redis.Redis` constructor
        :param kwargs: Keyword arguments to pass into the :class:`redis.Redis` constructor
        :raises exceptions.DatabaseAlreadyInitializedError: If the database connection has already
                                                            been opened.
        """
        if self._database is not None:
            raise ConnectionAlreadyInitializedError(
                "Database connection already opened"
            )
        self._database = redis.Redis(*args, **kwargs)

    def get(self, key: str, default: Optional[Any] = None) -> Optional[RedisValue]:
        """Emulates the ``dict.get()`` method for a redis database

        :param key: Key to retrieve from the database
        :param default: Default value to return if the key does not exist
        """
        try:
            return self[key]
        except KeyError:
            return default

    def pop(self, key: str, default: Optional[Any] = None) -> Optional[RedisValue]:
        """Emulates the ``dict.pop()`` method for a redis database

        :param key: Key to remove from the database
        :param default: Default value to return if the key does not exist
        """
        try:
            value = self[key]
            del self[key]
            return value
        except KeyError:
            return default

    def keys(self) -> List[str]:
        """Iterates over the keys in the database"""

        self._check_connection()
        keys = (
            self._database.keys()
            if self._namespace is None
            else self._database.keys(f"{self._namespace}:*")
        )
        return [
            key.decode().replace(f"{self._namespace}:", "")
            if self._namespace is not None
            else key.decode()
            for key in keys
        ]

    def values(self) -> Generator[RedisValue, None, None]:
        """Iterates over the values in the database

        .. warning:: Usage of this method is subject to potential race conditions in a multi-client
                     environment. The
                     `redis lock acquisition context manager <https://github.com/andymccurdy/redis-py#locks-as-context-managers>`_
                     is recommended to minimize the risk of conflicts.
        """
        for key in self.keys():
            yield self[key]

    def items(self) -> Generator[Tuple[str, RedisValue], None, None]:
        """Iterates over the key/value pairs in the database

        .. warning:: Usage of this method is subject to potential race conditions in a multi-client
                     environment. The
                     `redis lock acquisition context manager <https://github.com/andymccurdy/redis-py#locks-as-context-managers>`_
                     is recommended to minimize the risk of conflicts.
        """
        for key in self.keys():
            yield (key, self[key])

    def update(self, data: Dict[str, RedisValue]):
        """Updates the database from a dictionary, overwriting keys as necessary"""
        self._check_connection()
        for key, value in data.items():
            self._database.set(
                self._expand_key(key), json.dumps(value), ex=self.expiration_seconds
            )
