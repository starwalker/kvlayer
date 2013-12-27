import os
import sys
import json
import yaml
import time
import gzip
import pytest
import tempfile
import streamcorpus
from cStringIO import StringIO
from kvlayer.instance_collection import InstanceCollection, BlobCollection, register

class Thing(object):
    def __init__(self, blob=None):
        self.data = dict()
        if blob is not None:
            self.loads(blob)

    def dumps(self):
        return yaml.dump(self.data)

    def loads(self, blob):
        self.data = yaml.load(blob)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def do_more_things(self):
        self.data['doing'] = 'something'

class ThingSerializer(object):

    def __init__(self):
        self.config = {}

    def loads(self, blob):
        if self.config.get('compress') == 'gz':
            fh = StringIO(blob)
            gz = gzip.GzipFile(fileobj=fh, mode='rb')
            blob = gz.read()
        return Thing(blob)

    def dumps(self, thing):
        blob = thing.dumps()        
        if self.config.get('compress') == 'gz':
            fh = StringIO()
            gz = gzip.GzipFile(fileobj=fh, mode='wb')
            gz.write(blob)
            gz.flush()
            gz.close()
            blob = fh.getvalue()
        return blob

    def configure(self, config):
        self.config = config

def test_instance_collection():

    register('Thing', ThingSerializer)

    ic = InstanceCollection()
    ic.insert('thing1', Thing(yaml.dump(dict(hello='people'))), 'Thing')
    ic['thing1']['another'] = 'more'
    ic['thing1'].do_more_things()
    ic_str = ic.dumps()
    
    ic2 = InstanceCollection(ic_str)

    ## check laziness
    assert 'thing1' not in ic2._instances

    assert ic2['thing1']['another'] == 'more'
    assert 'thing1' in ic2._instances

    assert ic2['thing1']['hello'] == 'people'
    assert ic2['thing1']['doing'] == 'something'

def test_instance_collection_gzip():

    register('Thing', ThingSerializer)

    ic = InstanceCollection()
    ic.insert('thing1', Thing(yaml.dump(dict(hello='people'))), 'Thing', config=dict(compress='gz'))
    ic['thing1']['another'] = 'more'
    ic['thing1'].do_more_things()
    ic_str = ic.dumps()
    
    ic2 = InstanceCollection(ic_str)
    
    fh = StringIO(ic2._bc.typed_blobs['thing1'].blob)
    gz = gzip.GzipFile(fileobj=fh, mode='rb')
    blob = gz.read()
    tb_data = yaml.load(blob)
    assert 'hello' in tb_data
    assert isinstance(tb_data, dict)

    ## check laziness
    assert 'thing1' not in ic2._instances

    assert ic2['thing1']['another'] == 'more'
    assert 'thing1' in ic2._instances

    assert ic2['thing1']['hello'] == 'people'
    assert ic2['thing1']['doing'] == 'something'


def test_instance_collection_yaml_json():

    ic = InstanceCollection()
    ic.insert('thing2', dict(hello='people'), 'yaml')
    ic['thing2']['another'] = 'more'
    ic.insert('thing3', dict(hello='people2'), 'json')
    ic_str = ic.dumps()
    
    ic2 = InstanceCollection(ic_str)

    ## check laziness
    assert 'thing2' not in ic2._instances

    assert ic2['thing2']['another'] == 'more'
    assert 'thing2' in ic2._instances

    assert ic2['thing2']['hello'] == 'people'
    assert ic2['thing3']['hello'] == 'people2'


@pytest.mark.performance
def test_throughput_instance_collection():
    ic = InstanceCollection()
    ic.insert('thing1', Thing(yaml.dump(dict(one_mb=' ' * 2**20))), 'Thing')
    ic_str = ic.dumps()
    
    start_time = time.time()
    num = 100
    for i in range(num):
        ic2 = InstanceCollection(ic_str)
        ic2.dumps()
    elapsed = time.time() - start_time
    rate = float(num) / elapsed
    print '%d MB in %.1f sec --> %.1f MB per sec' % (num, elapsed, rate)
    assert rate > 100

@pytest.mark.xfail ## need to enhance streamcorpus.Chunk to have
                   ## 'wrapper' kwarg
def test_chunk_blob_collection():
    tmp = tempfile.NamedTemporaryFile(mode='wb')
    o_chunk = streamcorpus.Chunk(file_obj=tmp, mode='wb', message=BlobCollection)

    ic = InstanceCollection()
    ic['thing1'] = Thing(yaml.dump(dict(hello='people')))
    ic['thing1']['another'] = 'more'

    o_chunk.add(ic)
    tmp.flush()

    for ic2 in streamcorpus.Chunk(tmp.name, message=BlobCollection):
        pass

    assert ic['thing1'] == ic2['thing1']

    