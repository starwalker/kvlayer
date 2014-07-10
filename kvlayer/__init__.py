'''Database abstraction for key/value stores.

.. This software is released under an MIT/X11 open source license.
   Copyright 2012-2014 Diffeo, Inc.

Many popular large-scale databases export a simple key/value
abstraction: the database is simply a list of cells with some
(possibly structured) key and a value for each key.  This allows the
database system itself to partition the database in some way, and in a
distributed system it allows the correct system hosting a specific key
to be found easily.  This model is also simple enough that it can be
used with in-memory storage or more traditional SQL-based databases.

This module provides a simple abstraction around these
key/value-oriented databases.  It works with :mod:`yakonfig` to hold
basic configuration settings, so the top-level YAML configuration must
have a ``kvlayer`` block.  This will typically look something like

.. code-block:: yaml

    kvlayer:
      storage_type: redis
      storage_addresses: [redis.example.com:6379]
      app_name: app
      namespace: namespace

These four parameters are always required.  ``storage_type`` gives one
of the database backends described below.  ``storage_addresses`` is a
list of backend-specific database locations.  ``app_name`` and
``namespace`` combine to form a container for the virtual tables
stored in kvlayer.

Backends
========

local
-----

This is intended only for testing.  Values are stored in a Python
dictionary and not persisted.  ``storage_addresses`` are not required.

.. code-block:: yaml

    kvlayer:
      storage_type: local

filestorage
-----------

This is intended only for testing.  Values are stored in a local file
using the :mod:`shelve` module.  This does not require
``storage_addresses``, but it does require:

.. code-block:: yaml

    kvlayer:
      storage_type: filestorage

      # Name of the file to use for storage
      filename: /tmp/kvlayer.bin

      # If set, actually work on a copy of "filename" at this location.
      copy_to_filename: /tmp/kvlayer-copy.bin

redis
-----

Uses the `Redis`_ in-memory database.

.. code-block:: yaml

    kvlayer:
      storage_type: redis

      # host:port locations of Redis servers; only the first is used
      storage_addresses: [redis.example.com:6379]

      # Redis database number (default: 0)
      redis_db_num: 1

accumulo
--------

Uses the Apache `Accumulo`_ distributed database.  Your installation
must be running the Accumulo proxy, and the configuration points at
that proxy.

.. code-block:: yaml

    kvlayer:
      storage_type: accumulo

      # host:port location of the proxy; only the first is used
      storage_addresses: [accumulo.example.com:50096]
      username: root
      password: secret

      # all of the following parameters are default values and are optional
      accumulo_max_memory: 1000000
      accumulo_timeout_ms: 30000
      accumulo_threads: 10
      accumulo_latency_ms: 10
      thrift_framed_transport_size_in_mb: 15

Each kvlayer table is instantiated as an Accumulo table named
``appname_namespace_table``.

.. _Accumulo: http://accumulo.apache.org/

postgres
--------

Uses `PostgreSQL`_ for storage.  This backend is only available if the
:mod:`psycopg2` module is installed.  The ``storage_type`` is a
`PostgreSQL connection string`_.  The ``app_name`` and ``namespace``
can only consist of alphanumeric characters, underscores, or ``$``,
and must begin with a letter or underscore.

.. code-block:: yaml

    kvlayer:
      storage_type: postgres
      storage_addresses:
      - 'host=postgres.example.com port=5432 user=test dbname=test password=test'

      # all of the following parameters are default values and are optional
      # keep this many connections alive
      min_connections: 2
      # never create more than this many connections
      max_connections: 16
      # break large scans (using SQL) into chunks of this many
      scan_inner_limit: 1000

The backend assumes the user is able to run SQL ``CREATE TABLE`` and
``DROP TABLE`` statements.  Each kvlayer namespace is instantiated as
an SQL table named ``kv_appname_namespace``; kvlayer tables are
collections of rows within the namespace table sharing a common field.

Within the system, the ``min_connections`` and ``max_connections``
property apply per client object.  If ``min_connections`` is set to 0
then the connection pool will never hold a connection alive, which
typically adds a performance cost to reconnect.

.. _PostgreSQL: http://www.postgresql.org
.. _PostgreSQL connection string: http://www.postgresql.org/docs/current/static/libpq-connect.html#LIBPQ-PARAMKEYWORDS

riak
----

Uses `Riak`_ for storage.  This backend is only available if the
corresponding :mod:`riak` client library is installed.  Multiple
``storage_addresses`` are actively encouraged for this backend. Each
may be a simple string, or a dictionary containing keys ``host``,
``http_port``, and ``pb_port`` if your setup is using non-standard
port numbers.  A typical setup will look like:

.. code-block:: yaml

    kvlayer:
      storage_type: riak
      storage_addresses: [riak01, riak02, riak03, riak04, riak05]
      # optional settings with their default values
      protocol: pbc # or http or https
      scan_limit: 100

The setup from the Riak "Five-Minute Install" runs five separate
Riak nodes all on localhost, resulting in configuration like

.. code-block: yaml

    kvlayer:
      storage_type: riak
      storage_addresses:
      - { host: "127.0.0.1", pb_port: 10017, http_port: 10018 }
      - { host: "127.0.0.1", pb_port: 10027, http_port: 10028 }
      - { host: "127.0.0.1", pb_port: 10037, http_port: 10038 }
      - { host: "127.0.0.1", pb_port: 10047, http_port: 10048 }
      - { host: "127.0.0.1", pb_port: 10057, http_port: 10058 }

One :mod:`kvlayer` namespace corresponds to one Riak bucket.

The ``protocol`` setting selects between defaulting to the HTTP or
protocol buffer APIs.  While Riak's default is generally ``http``,
the ``pbc`` API seems to work equally well and is much faster.  The
kvlayer backend's default is ``pbc``.

The ``scan_limit`` setting determines how many results will be
returned from each secondary index search.  A higher setting for this
results in fewer network round-trips to get search results, but also
results in higher latency to return each.  This affects both calls to
the kvlayer scan API as well as calls to delete kvlayer tables, which
are also Riak key scans.

Your Riak cluster must be configured with secondary indexing enabled,
and correspondingly, must be using the LevelDB backend.  The default
bucket settings, and in particular setting ``allow_mult`` to
``false``, are correct for :mod:`kvlayer`.

cassandra
---------

Uses the Apache `Cassandra`_ distributed database.  Note that this
backend requires keys to be limited to tuples of UUIDs.

.. code-block:: yaml

    kvlayer:
      storage_type: cassandra
      storage_addresses: ['cassandra.example.com:9160']
      username: root
      password: secret

      connection_pool_size: 2
      max_consistency_delay: 120
      replication_factor: 1
      thrift_framed_transport_size_in_mb: 15

.. _Cassandra: http://cassandra.apache.org/

API
===

Having set up the global configuration, it is enough to call
:func:`kvlayer.client` to get a storage client object.

The API works in terms of "tables", though these are slightly
different from tradational database tables.  Each table has keys which
are tuples of a fixed length.

.. autofunction:: client

.. autoclass:: kvlayer._abstract_storage.AbstractStorage
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: DatabaseEmpty
.. autoclass:: BadKey

Instance Collections
====================

.. automodule:: kvlayer.instance_collection

'''

from kvlayer._client import client
from kvlayer.config import config_name, default_config, add_arguments, \
    runtime_keys, check_config
from kvlayer._exceptions import DatabaseEmpty, BadKey
