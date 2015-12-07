# -*- coding: utf-8 -*-

import unittest
import requests
import filecmp
import calendar
import time
import os

import oss2

from oss2.exceptions import (ClientError, RequestError,
                             NotFound, NoSuchKey, Conflict, PositionNotEqualToLength, ObjectNotAppendable)
from oss2 import to_string

from common import *


def now():
    return int(calendar.timegm(time.gmtime()))


class TestObject(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super(TestObject, self).__init__(*args, **kwargs)
        self.bucket = None

    def setUp(self):
        self.bucket = oss2.Bucket(oss2.Auth(OSS_ID, OSS_SECRET), OSS_ENDPOINT, OSS_BUCKET)
        self.bucket.create_bucket()

    def test_object(self):
        key = random_string(12) + '.js'
        content = random_bytes(1024)

        self.assertRaises(NotFound, self.bucket.head_object, key)

        lower_bound = now() - 60 * 16
        upper_bound = now() + 60 * 16

        def assert_result(result):
            self.assertEqual(result.content_length, len(content))
            self.assertEqual(result.content_type, 'application/javascript')
            self.assertEqual(result.object_type, 'Normal')

            self.assertTrue(result.last_modified > lower_bound)
            self.assertTrue(result.last_modified < upper_bound)

            self.assertTrue(result.etag)

        self.bucket.put_object(key, content)

        get_result = self.bucket.get_object(key)
        self.assertEqual(get_result.read(), content)
        assert_result(get_result)

        head_result = self.bucket.head_object(key)
        assert_result(head_result)

        self.assertEqual(get_result.last_modified, head_result.last_modified)
        self.assertEqual(get_result.etag, head_result.etag)

        self.bucket.delete_object(key)

        self.assertRaises(NoSuchKey, self.bucket.get_object, key)

    def test_file(self):
        filename = random_string(12) + '.js'
        filename2 = random_string(12)

        key = random_string(12) + '.txt'
        content = random_bytes(1024 * 1024)

        with open(filename, 'wb') as f:
            f.write(content)

        # 上传本地文件到OSS
        self.bucket.put_object_from_file(key, filename)

        # 检查Content-Type应该是javascript
        result = self.bucket.head_object(key)
        self.assertEqual(result.headers['content-type'], 'application/javascript')

        # 下载到本地文件
        self.bucket.get_object_to_file(key, filename2)

        self.assertTrue(filecmp.cmp(filename, filename2))

        # 上传本地文件的一部分到OSS
        key_partial = random_string(12) + '-partial.txt'
        offset = 100
        with open(filename, 'rb') as f:
            f.seek(offset, os.SEEK_SET)
            self.bucket.put_object(key_partial, f)

        # 检查上传后的文件
        result = self.bucket.get_object(key_partial)
        self.assertEqual(result.content_length, len(content) - offset)
        self.assertEqual(result.read(), content[offset:])

        # 清理
        os.remove(filename)
        os.remove(filename2)

    def test_streaming(self):
        src_key = random_string(12) + '.src'
        dst_key = random_string(12) + '.dst'

        content = random_bytes(1024 * 1024)

        self.bucket.put_object(src_key, content)

        # 获取OSS上的文件，一边读取一边写入到另外一个OSS文件
        src = self.bucket.get_object(src_key)
        self.bucket.put_object(dst_key, src)

        # verify
        self.assertEqual(self.bucket.get_object(src_key).read(), self.bucket.get_object(dst_key).read())

    def make_generator(self, content, chunk_size):
        def generator():
            offset = 0
            while offset < len(content):
                n = min(chunk_size, len(content) - offset)
                yield content[offset:offset+n]

                offset += n

        return generator()

    def test_data_generator(self):
        key = random_string(16)
        key2 = random_string(16)
        content = random_bytes(1024 * 1024 + 1)

        self.bucket.put_object(key, self.make_generator(content, 8192))
        self.assertEqual(self.bucket.get_object(key).read(), content)

        # test progress
        stats = {'previous': -1}

        def progress_callback(bytes_consumed, total_bytes):
            self.assertTrue(total_bytes is None)
            self.assertTrue(bytes_consumed > stats['previous'])

            stats['previous'] = bytes_consumed

        self.bucket.put_object(key2, self.make_generator(content, 8192), progress_callback=progress_callback)
        self.assertEqual(self.bucket.get_object(key).read(), content)

    def test_request_error(self):
        bad_endpoint = random_string(8) + '.' + random_string(16) + '.com'
        bucket = oss2.Bucket(oss2.Auth(OSS_ID, OSS_SECRET), bad_endpoint, OSS_BUCKET)

        try:
            bucket.get_bucket_acl()
        except RequestError as e:
            self.assertEqual(e.status, oss2.exceptions.OSS_REQUEST_ERROR_STATUS)
            self.assertEqual(e.request_id, '')
            self.assertEqual(e.code, '')
            self.assertEqual(e.message, '')

            self.assertTrue(str(e))
            self.assertTrue(e.body)

    def test_timeout(self):
        bucket = oss2.Bucket(oss2.Auth(OSS_ID, OSS_SECRET), OSS_ENDPOINT, OSS_BUCKET,
                             connect_timeout=0.001)
        self.assertRaises(RequestError, bucket.get_bucket_acl)

    def test_get_object_iterator(self):
        key = random_string(12)
        content = random_bytes(1024 * 1024)

        self.bucket.put_object(key, content)
        result = self.bucket.get_object(key)
        content_got = b''

        for chunk in result:
            content_got += chunk

        self.assertEqual(len(content), len(content_got))
        self.assertEqual(content, content_got)

    def test_anonymous(self):
        key = random_string(12)
        content = random_bytes(512)

        # 设置bucket为public-read，并确认可以上传和下载
        self.bucket.put_bucket_acl('public-read-write')
        time.sleep(2)

        b = oss2.Bucket(oss2.AnonymousAuth(), OSS_ENDPOINT, OSS_BUCKET)
        b.put_object(key, content)
        result = b.get_object(key)
        self.assertEqual(result.read(), content)

        # 测试sign_url
        url = b.sign_url('GET', key, 100)
        resp = requests.get(url)
        self.assertEqual(content, resp.content)

        # 设置bucket为private，并确认上传和下载都会失败
        self.bucket.put_bucket_acl('private')
        time.sleep(1)

        self.assertRaises(oss2.exceptions.AccessDenied, b.put_object, key, content)
        self.assertRaises(oss2.exceptions.AccessDenied, b.get_object, key)

    def test_range_get(self):
        key = random_string(12)
        content = random_bytes(1024)

        self.bucket.put_object(key, content)

        result = self.bucket.get_object(key, byte_range=(500, None))
        self.assertEqual(result.read(), content[500:])

        result = self.bucket.get_object(key, byte_range=(None, 199))
        self.assertEqual(result.read(), content[-199:])

        result = self.bucket.get_object(key, byte_range=(3, 3))
        self.assertEqual(result.read(), content[3:4])

    def test_list_objects(self):
        result = self.bucket.list_objects()
        self.assertEqual(result.status, 200)

    def test_batch_delete_objects(self):
        object_list = []
        for i in range(0, 5):
            key = random_string(12)
            object_list.append(key)

            self.bucket.put_object(key, random_string(64))

        result = self.bucket.batch_delete_objects(object_list)
        self.assertEqual(sorted(object_list), sorted(result.deleted_keys))

        for object in object_list:
            self.assertTrue(not self.bucket.object_exists(object))

    def test_batch_delete_objects_empty(self):
        try:
            self.bucket.batch_delete_objects([])
        except ClientError as e:
            self.assertEqual(e.status, oss2.exceptions.OSS_CLIENT_ERROR_STATUS)
            self.assertEqual(e.request_id, '')
            self.assertEqual(e.code, '')
            self.assertEqual(e.message, '')

            self.assertTrue(e.body)
            self.assertTrue(str(e))

    def test_append_object(self):
        key = random_string(12)
        content1 = random_bytes(512)
        content2 = random_bytes(128)

        result = self.bucket.append_object(key, 0, content1)
        self.assertEqual(result.next_position, len(content1))

        try:
            self.bucket.append_object(key, 0, content2)
        except PositionNotEqualToLength as e:
            self.assertEqual(e.next_position, len(content1))
        else:
            self.assertTrue(False)

        result = self.bucket.append_object(key, len(content1), content2)
        self.assertEqual(result.next_position, len(content1) + len(content2))

        self.bucket.delete_object(key)

    def test_private_download_url(self):
        for key in [random_string(12), u'中文文件名']:
            content = random_bytes(42)

            str_name = to_string(key)
            self.bucket.put_object(str_name, content)
            url = self.bucket.sign_url('GET', str_name, 60)

            resp = requests.get(url)
            self.assertEqual(content, resp.content)

    def test_copy_object(self):
        source_key = random_string(12)
        target_key = random_string(13)
        content = random_bytes(36)

        self.bucket.put_object(source_key, content)
        self.bucket.copy_object(self.bucket.bucket_name, source_key, target_key)

        result = self.bucket.get_object(target_key)
        self.assertEqual(content, result.read())

    def test_update_object_meta(self):
        key = random_string(12) + '.txt'
        content = random_bytes(36)

        self.bucket.put_object(key, content)

        # 更改Content-Type，增加用户自定义元数据
        self.bucket.update_object_meta(key, {'Content-Type': 'whatever',
                                                     'x-oss-meta-category': 'novel'})

        result = self.bucket.head_object(key)
        self.assertEqual(result.headers['content-type'], 'whatever')
        self.assertEqual(result.headers['x-oss-meta-category'], 'novel')

    def test_object_acl(self):
        key = random_string(12)
        content = random_bytes(32)

        self.bucket.put_object(key, content)
        self.assertEqual(self.bucket.get_object_acl(key).acl, oss2.OBJECT_ACL_DEFAULT)

        for permission in (oss2.OBJECT_ACL_PRIVATE, oss2.OBJECT_ACL_PUBLIC_READ, oss2.OBJECT_ACL_PUBLIC_READ_WRITE,
                           oss2.OBJECT_ACL_DEFAULT):
            self.bucket.put_object_acl(key, permission)
            self.assertEqual(self.bucket.get_object_acl(key).acl, permission)

        self.bucket.delete_object(key)

    def test_object_exists(self):
        key = random_string(12)

        self.assertTrue(not self.bucket.object_exists(key))

        self.bucket.put_object(key, "hello world")
        self.assertTrue(self.bucket.object_exists(key))

    def test_user_meta(self):
        key = random_string(12)

        self.bucket.put_object(key, 'hello', headers={'x-oss-meta-key1': 'value1',
                                                      'X-Oss-Meta-Key2': 'value2'})

        headers = self.bucket.get_object(key).headers
        self.assertEqual(headers['x-oss-meta-key1'], 'value1')
        self.assertEqual(headers['x-oss-meta-key2'], 'value2')

    def test_progress(self):
        stats = {'previous': -1}

        def progress_callback(bytes_consumed, total_bytes):
            self.assertTrue(bytes_consumed <= total_bytes)
            self.assertTrue(bytes_consumed > stats['previous'])

            stats['previous'] = bytes_consumed

        key = random_string(12)
        content = random_bytes(2 * 1024 * 1024)

        # 上传内存中的内容
        stats = {'previous': -1}
        self.bucket.put_object(key, content, progress_callback=progress_callback)
        self.assertEqual(stats['previous'], len(content))

        # 追加内容
        stats = {'previous': -1}
        self.bucket.append_object(random_string(12), 0, content, progress_callback=progress_callback)
        self.assertEqual(stats['previous'], len(content))

        # 下载到文件
        stats = {'previous': -1}
        filename = random_string(12) + '.txt'
        self.bucket.get_object_to_file(key, filename, progress_callback=progress_callback)
        self.assertEqual(stats['previous'], len(content))

        # 上传本地文件
        stats = {'previous': -1}
        self.bucket.put_object_from_file(key, filename, progress_callback=progress_callback)
        self.assertEqual(stats['previous'], len(content))

        # 下载到本地，采用iterator语法
        stats = {'previous': -1}
        result = self.bucket.get_object(key, progress_callback=progress_callback)
        content_got = b''
        for chunk in result:
            content_got += chunk
        self.assertEqual(stats['previous'], len(content))
        self.assertEqual(content, content_got)

        os.remove(filename)

    def test_exceptions(self):
        key = random_string(12)
        content = random_bytes(16)

        self.assertRaises(NotFound, self.bucket.get_object, key)
        self.assertRaises(NoSuchKey, self.bucket.get_object, key)

        self.bucket.put_object(key, content)

        self.assertRaises(Conflict, self.bucket.append_object, key, len(content), b'more content')
        self.assertRaises(ObjectNotAppendable, self.bucket.append_object, key, len(content), b'more content')

    def test_gzip_get(self):
        """OSS supports HTTP Compression, see https://en.wikipedia.org/wiki/HTTP_compression for details.
        """
        key = random_string(12) + '.txt'    # ensure our content-type is text/plain, which could be compressed
        content = random_bytes(1024 * 1024) # ensure our content-length is larger than 1024 to trigger compression

        self.bucket.put_object(key, content)

        result = self.bucket.get_object(key, headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(result.read(), content)
        self.assertTrue(result.content_length is None)
        self.assertEqual(result.headers['Content-Encoding'], 'gzip')

        # test progress
        stats = {'previous': -1}

        def progress_callback(bytes_consumed, total_bytes):
            self.assertTrue(total_bytes is None)
            self.assertTrue(bytes_consumed > stats['previous'])
            stats['previous'] = bytes_consumed

        content_got = b''
        result = self.bucket.get_object(key, headers={'Accept-Encoding': 'gzip'}, progress_callback=progress_callback)
        for chunk in result:
            content_got += chunk

        self.assertEqual(len(content), len(content_got))
        self.assertEqual(content, content_got)

if __name__ == '__main__':
    unittest.main()