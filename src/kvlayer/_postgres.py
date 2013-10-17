# easy_install psycopg2
# OR
# pip install psycopg2

import logging

import psycopg2

from ._abstract_storage import AbstractStorage
from ._utils import join_uuids, split_uuids
from ._exceptions import MissingID, BadKey


# kv_${namespace}
# table, key, value
_CREATE_TABLE = '''CREATE TABLE kv_%(namespace)s (
  t text,
  k text,
  v bytea,
  PRIMARY KEY (t, k)
);

CREATE FUNCTION upsert_%(namespace)s(tname TEXT, key TEXT, data BYTEA) RETURNS VOID AS
$$
BEGIN
    LOOP
        -- first try to update the key
        UPDATE kv_%(namespace)s SET v = data WHERE t = tname AND k = key;
        IF found THEN
            RETURN;
        END IF;
        -- not there, so try to insert the key
        -- if someone else inserts the same key concurrently,
        -- we could get a unique-key failure
        BEGIN
            INSERT INTO kv_%(namespace)s(t,k,v) VALUES (tname, key, data);
            RETURN;
        EXCEPTION WHEN unique_violation THEN
            -- Do nothing, and loop to try the UPDATE again.
        END;
    END LOOP;
END;
$$
LANGUAGE plpgsql;
'''

_DROP_TABLE = "DROP FUNCTION upsert_%(namespace)s(TEXT,TEXT,BYTEA)"
_DROP_TABLE_b = '''DROP TABLE kv_%(namespace)s'''

_CLEAR_TABLE = '''DELETE FROM kv_{namespace} WHERE t = %s'''

## Double-escaping %% because these strings are templates twice!
## First we build literal table/function name strings
## like kv_MyNamespace upsert_MyNamespace; then we pass values into psycopg2.

# use cursor.callproc() instead of SELECT query.
#_PUT = '''SELECT upsert_%(namespace)s (%%s, %%s, %%s);'''

#_GET = '''SELECT k, v FROM kv_{namespace} WHERE t = %s AND k = %s;'''

_GET_RANGE = '''SELECT k, v FROM kv_{namespace} WHERE t = %s AND k >= %s AND k <= %s;'''

_DELETE = '''DELETE FROM kv_%(namespace)s WHERE t = %%s AND k = %%s;'''

#_DELETE_RANGE = '''DELETE FROM kv_%(namespace)s WHERE t = %%s AND k >= %%s AND K <= %%s;'''


MAX_BLOB_BYTES = 15000000


def _cursor_check_namespace_table(cursor, namespace):
    cursor.execute('SELECT 1 FROM pg_tables WHERE tablename = %s', ('kv_' + namespace,))
    return cursor.rowcount > 0


class PGStorage(AbstractStorage):
    def __init__(self, config):
        '''Initialize a storage instance for namespace.
        uses the single string specifier for a connectionn to a postgres db
postgresql://[user[:password]@][netloc][:port][/dbname][?param1=value1&...]
        '''
        self.config = config
        self.namespace = config['namespace']
        assert self.namespace, 'postgres kvlayer needs config["namespace"]'
        self.storage_addresses = config['storage_addresses']
        assert self.storage_addresses, 'postgres kvlayer needs config["storage_addresses"]'
        #self.username = conifg['username']
        #self.password = config['password']
        self.connection = None
        self.tablespecs = {}  # map {table name: num uuids in key}

    def _conn(self):
        '''internal lazy connector'''
        if self.connection is None:
            self.connection = psycopg2.connect(self.storage_addresses[0])
            self.connection.autocommit = True
        return self.connection

    def _namespace_table_exists(self):
        conn = self._conn()
        with conn.cursor() as cursor:
            return _cursor_check_namespace_table(cursor, self.namespace)

    def setup_namespace(self, table_names):
        '''creates tables in the namespace.  Can be run multiple times with
        different table_names in order to expand the set of tables in
        the namespace.

        :param table_names: Each string in table_names becomes the
        name of a table, and the value must be an integer specifying
        the number of UUIDs in the keys

        :type table_names: dict(str = int)
        '''
        self.tablespecs.update(table_names)
        conn = self._conn()
        with conn.cursor() as cursor:
            if _cursor_check_namespace_table(cursor, self.namespace):
                # already exists
                logging.debug('namespace %r already exists, not creating', self.namespace)
                return
            cursor.execute(_CREATE_TABLE % {'namespace': self.namespace})

    def delete_namespace(self):
        '''Deletes all data from namespace.'''
        conn = self._conn()
        with conn.cursor() as cursor:
            if not _cursor_check_namespace_table(cursor, self.namespace):
                logging.debug('namespace %r does not exist, not dropping', self.namespace)
                return
            try:
                cursor.execute(_DROP_TABLE % {'namespace': self.namespace})
                cursor.execute(_DROP_TABLE_b % {'namespace': self.namespace})
            except:
                logging.warn('error on delete_namespace(%r)', self.namespace, exc_info=True)

    def clear_table(self, table_name):
        'Delete all data from one table'
        conn = self._conn()
        with conn.cursor() as cursor:
            cursor.execute(
                _CLEAR_TABLE.format(namespace=self.namespace),
                (table_name,)
            )

    def put(self, table_name, *keys_and_values, **kwargs):
        '''Save values for keys in table_name.  Each key must be a
        tuple of UUIDs of the length specified for table_name in
        setup_namespace.

        :params batch_size: a DB-specific parameter that limits the
        number of (key, value) paris gathered into each batch for
        communication with DB.
        '''
        num_uuids = self.tablespecs[table_name]
        conn = self._conn()
        with conn.cursor() as cursor:
            for kv in keys_and_values:
                if len(kv[0]) != num_uuids:
                    raise BadKey('invalid key has %s uuids but wanted %s: %r' % (len(kv[0]), num_uuids, kv[0]))
                cursor.callproc(
                    'upsert_%(namespace)s' % {'namespace': self.namespace},
                    (table_name, join_uuids(*kv[0]), kv[1]))

    def get(self, table_name, *key_ranges, **kwargs):
        '''Yield tuples of (key, value) from querying table_name for
        items with keys within the specified ranges.  If no key_ranges
        are provided, then yield all (key, value) pairs in table.

        :type key_ranges: (((UUID, ...), (UUID, ...)), ...)
                            ^^^^^^^^^^^^^^^^^^^^^^^^
                            start        finish of one range
        '''
        num_uuids = self.tablespecs[table_name]
        failOnEmptyResult = True
        if not key_ranges:
            key_ranges = [['', '']]
            failOnEmptyResult = False
        def _pgkeyrange(kr):
            return (table_name, kmin, kmax)
        cmd = _GET_RANGE.format(namespace=self.namespace)
        conn = self._conn()
        with conn.cursor() as cursor:
            for kr in key_ranges:
                kmin = kr[0]
                kmin = join_uuids(*kmin, num_uuids=num_uuids, padding='0')
                kmax = kr[1]
                kmax = join_uuids(*kmax, num_uuids=num_uuids, padding='f')
                logging.debug('pg t=%r %r<=k<=%r', table_name, kmin, kmax)
                cursor.execute(cmd, (table_name, kmin, kmax))
                logging.debug('%r rows from %r', cursor.rowcount, cmd)
                if not (cursor.rowcount > 0):
                    continue
                results = cursor.fetchmany()
                while results:
                    for row in results:
                        val = row[1]
                        if isinstance(val, buffer):
                            if len(val) > MAX_BLOB_BYTES:
                                logging.error('key=%r has blob of size %r over limit of %r', row[0], len(val), MAX_BLOB_BYTES)
                                continue  # TODO: raise instead of drop?
                            val = val[:]
                        yield split_uuids(row[0]), val
                        failOnEmptyResult = False
                    results = cursor.fetchmany()
        if failOnEmptyResult:
            raise MissingID()

    def delete(self, table_name, *keys, **kwargs):
        '''Delete all (key, value) pairs with specififed keys

        :params batch_size: a DB-specific parameter that limits the
        number of (key, value) paris gathered into each batch for
        communication with DB.
        '''
        num_uuids = self.tablespecs[table_name]
        def _delkey(k):
            if len(k) != num_uuids:
                raise Exception('invalid key has %s uuids but wanted %s: %r' % (len(k), num_uuids, k))
            return (table_name, join_uuids(k))
        conn = self._conn()
        with conn.cursor() as cursor:
            cursor.executemany(
                _DELETE % {'namespace': self.namespace},
                map(_delkey, keys))
                

    def close(self):
        '''
        close connections and end use of this storage client
        '''
        if self.connection:
            self.connection.close()
            self.connection = None


# run this to cleanup any cruft from kvlayer unit tests
CLEAN_TESTS = '''
CREATE OR REPLACE FUNCTION clean_tests() RETURNS VOID AS
$$
DECLARE
  argtypes text;
  tat text;
  toid oid;
  pnargs pg_proc%ROWTYPE;
  pt pg_tables%ROWTYPE;
BEGIN
  FOR pnargs IN SELECT * from pg_proc where proname like '%upsert_test_%' LOOP
    SELECT typname FROM pg_type WHERE oid = pnargs.proargtypes[0] INTO argtypes;
    FOR i in 1..array_upper(pnargs.proargtypes,1) LOOP
      SELECT typname FROM pg_type WHERE oid = pnargs.proargtypes[i] INTO tat;
      argtypes := argtypes || ',' || tat;
    END LOOP;
    EXECUTE 'DROP FUNCTION ' || pnargs.proname || '(' || argtypes || ');';
  END LOOP;
  FOR pt IN SELECT * FROM pg_tables WHERE tablename LIKE '%kv_test_%' LOOP
    EXECUTE 'DROP TABLE ' || pt.tablename || ';';
  END LOOP;
END
$$ LANGUAGE plpgsql;


SELECT clean_tests();
'''
