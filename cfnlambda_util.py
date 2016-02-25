"""
This module provides a utility function, generate_request, to create events
for using in testing.
"""

EXAMPLE_REQUEST = {
   "RequestType" : "Create",
   "ResponseURL" : "http://pre-signed-S3-url-for-response",
   "StackId" : "arn:aws:cloudformation:us-west-2:EXAMPLE/stack-name/guid",
   "RequestId" : "7bfe2d54710d48dcbc6f0b26fb68c9d1",
   "ResourceType" : "Custom::ResourceTypeName",
   "LogicalResourceId" : "MyLogicalResourceId",
   "ResourceProperties" : {
      "Key" : "Value",
      "List" : [ "1", "2", "3" ]
   }
}

def generate_request(request_type, resource_type, properties, response_url,
        stack_id=None,
        request_id=None,
        logical_resource_id=None,
        physical_resource_id=None,
        old_properties=None):
    """Generate a request for testing.

    Args:
        request_type: One of 'Create', 'Update', or 'Delete'.
        resource_type: The CloudFormation resource type. If it does not begin
            with 'Custom::', this is prepended.
        properties: A dictionary of the fields for the resource.
        response_url: A url or a tuple of (bucket, key) for S3. If key ends with
            'RANDOM', a random string replaces that.
    """

    import uuid
    import boto3

    request_type = request_type.lower()
    if request_type not in ['create', 'update', 'delete']:
        raise ValueError('unknown request type')
    request_type = request_type[0].upper() + request_type[1:]

    if not resource_type.startswith('Custom::'):
        resource_type = 'Custom::' + resource_type

    if not isinstance(properties, dict):
        raise TypeError('properties must be a dict')

    if isinstance(response_url, (list, tuple)):
        bucket, key = response_url
        if key.endswith('RANDOM'):
            key = key[:-6] + str(uuid.uuid4())
        response_url = boto3.client('s3').generate_presigned_url(
                ClientMethod='put_object',
                HttpMethod='PUT',
                Params={
                    'Bucket': bucket,
                    'Key': key})

    stack_id = stack_id or "arn:aws:cloudformation:us-west-2:EXAMPLE/stack-name/guid"

    request_id = request_id or str(uuid.uuid4())

    logical_resource_id = logical_resource_id or "MyLogicalResourceId"

    physical_resource_id = physical_resource_id or logical_resource_id

    event = {
           "RequestType" : request_type,
           "ResponseURL" : response_url,
           "StackId" : stack_id,
           "RequestId" : request_id,
           "ResourceType" : resource_type,
           "LogicalResourceId" : logical_resource_id,
           "ResourceProperties" : properties
           }

    if request_type in ['Update', 'Delete']:
        if not physical_resource_id:
            raise RuntimeError('physical resource id not set for {}'.format(request_type))
        event['PhysicalResourceId'] = physical_resource_id

    if request_type == 'Update':
        if not old_properties:
            raise RuntimeError('old properties not set for {}'.format(request_type))
        event['OldResourceProperties'] = old_properties

    return event

class MockLambdaContext(object):
    def __init__(self,
            function_name='FunctionName',
            function_version='$LATEST',
            memory_size=128,
            timeout=3,
            start=None,
            region='us-east-1',
            account_id='123456789012'):
        import time, uuid

        if start is None:
            start = time.time()

        self._timeout = timeout
        self._start = start
        self._get_time = time.time

        self.function_name = function_name
        self.function_version = function_version
        self.invoked_function_arn = 'arn:aws:lambda:{region}:{account_id}:function:{name}:{version}'.format(
                name=self.function_name,
                version=self.function_version,
                region=region,
                account_id=account_id)
        self.memory_limit_in_mb = memory_size
        self.aws_request_id = str(uuid.uuid4())
        self.log_group_name = '/aws/lambda/{}'.format(self.function_name)
        self.log_stream_name = '{}/[{}]{}'.format(
            time.strftime('%Y/%m/%d', time.gmtime(self._start)),
            self.function_version,
            self.aws_request_id)
        self.identity = None
        self.client_context = None

    def get_remaining_time_in_millis(self):
        time_used = self._get_time() - self._start
        time_left = self._timeout * 1000 - time_used
        return int(round(time_left * 1000))
