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
    
    The child class does not need to have a constructor. In this case, the resource
    type name, which is validated by handle() method, is 'Custom::' + the child 
    class name. The logger also uses the child class name. If either of these need
    to be different, they can be provided to the parent constructor.
    
    Child classes must implement the create(), update(), and delete() methods.
    Each of these methods can indicate success or failure in one of two ways:
    * Simply return or raise an exception
    * Set self.status to self.STATUS_SUCCESS or self.STATUS_FAILED
        In the case of failure, self.failure_reason can be set to a string to
        provide an explanation in the response.
    These methods can also populate the self.resource_outputs dictionary with fields
    that then will be available in CloudFormation. If the return value of the function
    is a dict, that is merged into resource_outputs. If it is not a dict, the value
    is stored under the 'result' key.
    
    Child classes may implement validate() and/or populate(). validate() should return
    True if self.resource_properties is valid. populate() can transfer the contents of
    self.resource_properties into object fields, if this is not done by validate().
    
    The class provides methods get_boto3_client() and get_boto3_resource() that cache
    the clients/resources in the class, reducing overhead in the Lambda invocations.
    These also rely on the get_boto3_session() method, which in turn uses
    BOTO3_SESSION_FACTORY if it is set, allowing overriding with mock sessions for
    testing.
    
    Some hooks are provided to override behavior. The first four are instance fields,
    since they may be set to functions that rely on instance fields. The last
    is a class field, since it is called by a class method.
    * finish_function, normally set to CloudFormationCustomResource.cfn_response, takes
        as input the custom resource object and deals with sending the response and 
        cleaning up.
    * send_function, used within CloudFormationCustomResource.cfn_response, takes as
        input the custom resource object, a url, and the response_content dictionary.
        Normally this is set to CloudFormationCustomResource.send_response, which uses
        requests to send the content to its destination. requests is loaded either
        directly if available, falling back to the vendored version in botocore.
    * generate_unique_id_prefix_function can be set to put a prefix on the id returned
        by generate_unique_id, for example if the physical resource
        id needs to be an ARN.
    * generate_physical_resource_id_function is used to get a physical resource id
        on a create call unless DISABLE_PHYSICAL_RESOURCE_ID_GENERATION is True.
        It takes the custom resource object as input.This is normally
        set to CloudFormationCustomResource.generate_unique_id, which
        generates a physical resource id like CloudFormation:
        {stack_id}-{logical resource id}-{random string}
        It also provides two keyword arguments:
        * prefix: if for example the physical resource id must be an arn
        * separator: defaulting to '-'.
    * BOTO3_SESSION_FACTORY takes no input and returns an object that acts like a boto3 session.
        If this class field is not None, it is used by get_boto3_session() instead of creating
        a regular boto3 session. This could be made to use placebo for testing
        https://github.com/garnaat/placebo

    The class provides four configuration options that can be overridden in child
    classes:
    * DELETE_LOGS_ON_STACK_DELETION: A boolean which, when True, will cause a successful
        stack deletion to trigger the deletion of the CloudWatch log group on stack
        deletion. If there is a problem during stack deletion, the logs are left in place.
        NOTE: this is not intended for use when the Lambda function is used by multiple
        stacks.
    * HIDE_STACK_DELETE_FAILURE: A boolean which, when True, will report
        SUCCESS to CloudFormation when a stack deletion is requested
        regardless of the success of the AWS Lambda function. This will
        prevent stacks from being stuck in DELETE_FAILED states but will
        potentially result in resources created by the AWS Lambda function
        to remain in existence after stack deletion. If
        HIDE_STACK_DELETE_FAILURE is False, an exception in the AWS Lambda
        function will result in DELETE_FAILED upon an attempt to delete
        the stack.
    * DISABLE_PHYSICAL_RESOURCE_ID_GENERATION: If True, skips the automatic generation
        of a unique physical resource id if the custom resource has a source for that
        itself.
    * PHYSICAL_RESOURCE_ID_MAX_LEN: An int used by generate_unique_id
        when generating a physical resource id.
    """
    DELETE_LOGS_ON_STACK_DELETION = False
    HIDE_STACK_DELETE_FAILURE = True
    
    DISABLE_PHYSICAL_RESOURCE_ID_GENERATION = False
    PHYSICAL_RESOURCE_ID_MAX_LEN = 128
    
    STATUS_SUCCESS = 'SUCCESS'
    STATUS_FAILED = 'FAILED'
    
    REQUEST_CREATE = 'Create'
    REQUEST_DELETE = 'Delete'
    REQUEST_UPDATE = 'Update'
    
    def __init__(self, resource_type=None, logger=None):
        import logging
        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger(self.__class__.__name__)
        self._base_logger = logging.getLogger('CFCustomResource')
        
        if not resource_type:
            resource_type = self.__class__.__name__
        
        def process_resource_type(resource_type):
            if not (resource_type.startswith('Custom::') or resource_type == 'AWS::CloudFormation::CustomResource'):
                resource_type = 'Custom::' + resource_type
            return resource_type
        
        if isinstance(resource_type, (list, tuple)):
            resource_type = [process_resource_type(rt) for rt in resource_type]
        elif isinstance(resource_type, basestring):
            resource_type = process_resource_type(resource_type)
        
        self.resource_type = resource_type
        
        self.request_resource_type = None
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
        
        self.generate_unique_id_prefix_function = None
        self.generate_physical_resource_id_function = self.generate_unique_id
        
    def validate_resource_type(self, resource_type):
        """Return True if resource_type is valid""" 
        if isinstance(self.resource_type, (list, tuple)):
            return resource_type in self.resource_type
        return resource_type == self.resource_type
    
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
    
    BOTO3_SESSION = None
    BOTO3_SESSION_FACTORY = None
    BOTO3_CLIENTS = None
    BOTO3_RESOURCES = None
    
    @classmethod
    def get_boto3_session(cls):
        if cls.BOTO3_SESSION is None:
            if cls.BOTO3_SESSION_FACTORY:
                cls.BOTO3_SESSION = cls.BOTO3_SESSION_FACTORY()
            else:
                import boto3
                cls.BOTO3_SESSION = boto3.session.Session()
        return cls.BOTO3_SESSION
    
    @classmethod
    def get_boto3_client(cls, name):
        if name not in cls.BOTO3_CLIENTS:
            cls.BOTO3_CLIENTS[name] = cls.get_boto3_session().client(name)
        return cls.BOTO3_CLIENTS[name]
    
    @classmethod
    def get_boto3_resource(cls, name):
        if name not in cls.BOTO3_RESOURCES:
            cls.BOTO3_RESOURCES[name] = cls.get_boto3_session().resource(name)
        return cls.BOTO3_RESOURCES[name]
    
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
        
        self.request_type = event['RequestType']
        self.response_url = event['ResponseURL']
        self.stack_id = event['StackId']
        self.request_id = event['RequestId']
        
        self.logical_resource_id = event['LogicalResourceId']
        self.physical_resource_id = event.get('PhysicalResourceId')
        self.resource_properties = event.get('ResourceProperties', {})
        self.old_resource_properties = event.get('OldResourceProperties')
        
        try:
            self.request_resource_type = event['ResourceType']
            if not self.validate_resource_type(self.request_resource_type):
                raise Exception('invalid resource type')
        
            if not self.validate():
                pass
            
            if not self.physical_resource_id and not self.DISABLE_PHYSICAL_RESOURCE_ID_GENERATION:
                self.physical_resource_id = self.generate_physical_resource_id_function(max_len=self.PHYSICAL_RESOURCE_ID_MAX_LEN)
            
            self.populate()
            
            outputs = getattr(self, self.request_type.lower())()
            
            if outputs:
                if not isinstance(outputs, dict):
                    outputs = {'result': outputs}
                self.resource_outputs.update(outputs)
            
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

            if self.status == self.STATUS_SUCCESS and self.DELETE_LOGS_ON_STACK_DELETION:
                import logging
                logging.disable(logging.CRITICAL)
                logs_client = self.get_boto3_client('logs')
                logs_client.delete_log_group(
                    logGroupName=context.log_group_name)
        
        self.finish_function(self)
    
    def generate_unique_id(self, prefix=None, separator='-', max_len=None):
        """Generate a unique id similar to how CloudFormation generates
        physical resource ids"""
        import random
        import string
        
        if prefix is None:
            if self.generate_unique_id_prefix_function:
                prefix = self.generate_unique_id_prefix_function()
            else:
                prefix = ''
    
        stack_id = self.stack_id.split(':')[-1]
        if '/' in stack_id:
            stack_id = stack_id.split('/')[1]
        stack_id = stack_id.replace('-', '')
    
        logical_resource_id = self.logical_resource_id
    
        len_of_rand = 12
        
        rand = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(len_of_rand))
        
        if max_len:
            max_len = max_len-len(prefix)
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
            rand=rand,
            )
    
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
            return resource.send_response_function(resource, resource.response_url, response_content)
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
