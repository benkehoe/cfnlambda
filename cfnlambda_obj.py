"""Base class for implementing Lambda functions backing custom CloudFormation resources.

The class, CloudFormationCustomResource, has methods that child classes
implement to create, update, or delete the resource, while taking care of the
parsing of the input, exception handling, and response sending. The class does
all of its importing inside its methods, so it can be copied over to, for
example, write the Lambda function in the browser-based editor, or inline in
CloudFormation once it supports that for Python.

The module also provides a utilty function, generate_request, to create events
for using in testing.

This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

class CloudFormationCustomResource(object):
    """Base class for CloudFormation custom resource classes.
    
    To create a handler for a custom resource in CloudFormation, simply create a
    child class (say, MyCustomResource), implement the methods specified below,
    and implement the handler function:
    def handler(event, context):
        MyCustomResource().handle(event, context)
    
    The constructor takes the resource type name, which it will validate for 
    incoming events. Optionally, a logger can be provided to the constructor;
    otherwise, the logger will use the child class name.
    
    Child classes must implement the create(), update(), and delete() methods.
    Each of these methods can indicate success or failure in one of two ways:
    * Simply return or raise an exception
    * Set self.status to self.STATUS_SUCCESS or self.STATUS_FAILED
        In the case of failure, self.failure_reason can be set to a string to
        provide an explanation in the response.
    These methods can also populate the self.resource_outputs dictionary with fields
    that then will be available in CloudFormation
    
    Child classes may implement validate() and/or populate(). validate() should return
    True if self.resource_properties is valid. populate() can transfer the contents of
    self.resource_properties into object fields, if this is not done by validate().
    
    Three hooks are provided to override behavior:
    * finish_function, normally set to CloudFormationCustomResource.cfn_response, takes
        as input the custom resource object and deals with sending the response and 
        cleaning up.
    * send_function, used within CloudFormationCustomResource.cfn_response, takes as
        input the custom resource object, a url, and the response_content dictionary.
        Normally this is set to CloudFormationCustomResource.send_response, which uses
        requests to send the content to its destination. requests is loaded either
        directly if available, falling back to the vendored version in botocore.
    * generate_physical_resource_id_function is used to get a physical resource id
        on a create call. It takes the custom resource object as input.This is normally
        set to CloudFormationCustomResource.generate_unique_physical_resource_id, which
        generates a physical resource id like CloudFormation:
        {stack_id}-{logical resource id}-{random string}
        It also provides two keyword arguments:
        * prefix: if for example the physical resource id must be an arn
        * separator: defaulting to '-'.
    * get_boto3_function takes no input and returns the boto3 module. This is used in
        CloudFormationCustomResource.cfn_response for deleting the logs (if DELETE_LOGS
        is set to True). This function could be replaced to use placebo https://github.com/garnaat/placebo

    The class provides three configuration options that can be overridden in child
    classes:
    * DELETE_LOGS: A boolean which, when True, will cause a successful
        stack deletion to trigger the deletion of the CloudWatch logs that
        were generated. If delete_logs is False or if there is a problem
        during stack deletion, the logs are left in place.
    * HIDE_STACK_DELETE_FAILURE: A boolean which, when True, will report
        SUCCESS to CloudFormation when a stack deletion is requested
        regardless of the success of the AWS Lambda function. This will
        prevent stacks from being stuck in DELETE_FAILED states but will
        potentially result in resources created by the AWS Lambda function
        to remain in existence after stack deletion. If
        hide_stack_delete_failure is False, an exception in the AWS Lambda
        function will result in DELETE_FAILED upon an attempt to delete
        the stack.
    * PHYSICAL_RESOURCE_ID_MAX_LEN: An int used by generate_unique_physical_resource_id
        when generating a physical resource id.
    """
    DELETE_LOGS = True
    HIDE_STACK_DELETE_FAILURE = True
    
    PHYSICAL_RESOURCE_ID_MAX_LEN = 128
    
    STATUS_SUCCESS = 'SUCCESS'
    STATUS_FAILED = 'FAILED'
    
    REQUEST_CREATE = 'Create'
    REQUEST_DELETE = 'Delete'
    REQUEST_UPDATE = 'Update'
    
    def __init__(self, resource_type, logger=None):
        import logging
        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger(self.__class__.__name__)
        self._base_logger = logging.getLogger('CFCustomResource')
        
        if not (resource_type.startswith('Custom::') 
                or resource_type == 'AWS::CloudFormation::CustomResource'):
            resource_type = 'Custom::' + resource_type
        
        self.resource_type = resource_type
        
        self.request_type = None
        self.response_url = None
        self.stack_id = None
        self.request_id = None
        
        self.logical_resource_id = None
        self.physical_resource_id = None
        self.resource_properties = None
        self.old_resource_properties = None
        
        self.event = None
        self.context = None
        
        self.status = None
        self.failure_reason = None
        self.resource_outputs = {}
        
        self.finish_function = self.cfn_response
        self.send_response_function = self.send_response
        
        self.generate_physical_resource_id_function = self.get_mangled_physical_resource_id
        
        self.get_boto3_function = self.get_boto3
        
    def validate(self):
        """Return True if self.resource_properties is valid."""
        return True
    
    def populate(self):
        """Populate fields from self.resource_properties and self.old_resource_properties,
        if this is not done in validate()"""
        pass
    
    def create(self):
        raise NotImplementedError
    
    def update(self):
        raise NotImplementedError
    
    def delete(self):
        raise NotImplementedError
    
    def handle(self, event, context):
        """Wrap this in a bare function to allow Lambda to call it"""
        import json
        self._base_logger.info('REQUEST RECEIVED: %s' % json.dumps(event))
        def plainify(obj):
            d = {}
            for field, value in vars(obj).iteritems():
                if isinstance(value,
                          (str, unicode,
                           int, float, bool, type(None))):
                    d[field] = value
                elif isinstance(value, (list, tuple)):
                    d[field] = [plainify(v) for v in value]
                elif isinstance(value, dict):
                    d[field] = dict((k, plainify(v)) for k, v in value.iteritems())
                else:
                    d[field] = repr(value)
        self._base_logger.info('LambdaContext: %s' % json.dumps(plainify(context)))
        
        self.event = event
        self.context = context
        
        resource_type_in_event = event['ResourceType']
        if resource_type_in_event != self.resource_type:
            raise Exception('invalid resource type')
        
        self.request_type = event['RequestType']
        self.response_url = event['ResponseURL']
        self.stack_id = event['StackId']
        self.request_id = event['RequestId']
        
        self.logical_resource_id = event['LogicalResourceId']
        self.physical_resource_id = event.get('PhysicalResourceId')
        self.resource_properties = event.get('ResourceProperties', {})
        self.old_resource_properties = event.get('OldResourceProperties')
        
        try:
            if not self.validate():
                pass
            
            if not self.physical_resource_id:
                self.physical_resource_id = self.generate_physical_resource_id_function(self)
            
            self.populate()
            
            getattr(self, self.request_type.lower())()
            if not self.status:
                self.status = self.STATUS_SUCCESS
        except Exception, e:
            import traceback
            if not self.status:
                self.status = self.STATUS_FAILED
                self.failure_reason = 'Custom resource %s failed due to exception "%s".' % (self.__class__.__name__, e.message)
            if self.failure_reason:
                self._base_logger.error(str(self.failure_reason))
            self._base_logger.debug(traceback.format_exc())
            
        if self.request_type == self.REQUEST_DELETE:
            if self.status == self.STATUS_FAILED and self.HIDE_STACK_DELETE_FAILURE:
                message = (
                    'There may be resources created by the AWS '
                    'Lambda that have not been deleted and cleaned up '
                    'despite the fact that the stack status may be '
                    'DELETE_COMPLETE.')
                self._base_logger.error(message)
                if self.failure_reason:
                    self._base_logger.error('Reason for failure: ' + str(self.failure_reason))
                self.status = self.STATUS_SUCCESS

            if self.status == self.STATUS_SUCCESS and self.DELETE_LOGS:
                import logging
                boto3 = self.get_boto3_function()
                logging.disable(logging.CRITICAL)
                logs_client = boto3.client('logs')
                logs_client.delete_log_stream(
                    logGroupName=context.log_group_name,
                    logStreamName=context.log_stream_name)
        
        self.finish_function(self)
    
    @classmethod
    def generate_unique_physical_resource_id(cls, resource, prefix='', separator='-'):
        """Generate a unique physical resource id similar to how CloudFormation does"""
        import random
        import string
    
        stack_id = resource.stack_id.split(':')[-1]
        if '/' in stack_id:
            stack_id = stack_id.split('/')[1]
        stack_id = stack_id.replace('-', '')
    
        max_len = resource.PHYSICAL_RESOURCE_ID_MAX_LEN-len(prefix)
        
        logical_resource_id = resource.logical_resource_id
    
        len_of_rand = 12
        len_of_parts = max_len - len_of_rand - 2 * len(separator)
        len_of_parts_diff = (len(stack_id) + len(logical_resource_id)) - len_of_parts
        if len_of_parts_diff > 0:
            len_of_stack_id = min(len(stack_id), len(stack_id) - len_of_parts_diff // 2)
            len_of_resource = len_of_parts - len_of_stack_id
            stack_id = stack_id[:len_of_stack_id]
            logical_resource_id = logical_resource_id[:len_of_resource]
        return '{prefix}{stack_id}{separator}{logical_id}{separator}{rand}'.format(
            prefix=prefix,
            separator=separator,
            stack_id=stack_id,
            logical_id=logical_resource_id,
            rand=''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(len_of_rand)),
            )
    
    @classmethod
    def get_boto3(cls):
        import boto3
        return boto3
    
    @classmethod
    def send_response(cls, resource, url, response_content):
        import httplib, json
        try:
            import requests
        except:
            from botocore.vendored import requests
        
        put_response = requests.put(resource.response_url,
                                    data=json.dumps(response_content))
        body_text = ""
        if put_response.status_code // 100 != 2:
            body_text = "\n" + put_response.text
        resource._base_logger.debug("Status code: %s %s%s" % (put_response.status_code, httplib.responses[put_response.status_code], body_text))
            
        return put_response
    
    @classmethod
    def cfn_response(cls, resource):
        import json, traceback   
        
        physical_resource_id = resource.physical_resource_id
        if physical_resource_id is None:
            physical_resource_id = resource.context.log_stream_name
        default_reason = ("See the details in CloudWatch Log Stream: %s" %
                       resource.context.log_stream_name)
        response_content = {
            "Status": resource.status,
            "Reason": resource.failure_reason or default_reason,
            "PhysicalResourceId": physical_resource_id,
            "StackId": resource.event['StackId'],
            "RequestId": resource.event['RequestId'],
            "LogicalResourceId": resource.event['LogicalResourceId'],
            "Data": resource.resource_outputs
        }
        resource._base_logger.debug("Response body: %s", json.dumps(response_content))
        try:
            return resource.send_response_function(resource, resource.reponse_url, response_content)
        except Exception as e:
            resource._base_logger.error("send response failed: %s" % e.message)
            resource._base_logger.debug(traceback.format_exc())

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
    import uuid, boto3
    
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
            raise RuntimeError('physical resource id not set for %s' % request_type)
        event['PhysicalResourceId'] = physical_resource_id
    
    if request_type == 'Update':
        if not old_properties:
            raise RuntimeError('old properties not set for %s' % request_type)
        event['OldResourceProperties'] = old_properties
    
    return event