cfnlambda
=========

:code:`cfnlambda` provides an abstract base class to make it easier to implement
`AWS CloudFormation custom resources`_. It was forked from Gene Wood's
`library of the same name`_, which provides lower-level functions and 
decorators for the same purpose.

The :code:`CloudFormationCustomResource` class requires a child class to implement
three methods: :code:`create`, :code:`update`, and :code:`delete`, for each of the respective
CloudFormation actions. Failure can be indicated by raising an exception.
Logging to CloudWatch is provided by the :code:`logger` field.

Quickstart
----------

::

    from cfnlambda import CloudFormationCustomResource
	
	class Adder(CloudFormationCustomResource):
	    def create(self):
            sum = (float(self.resource_properties['key1']) + 
                   float(self.resource_properties['key2']))
            return {'sum': sum}
        
        update = create
        
        def delete(self):
            pass
    
    def handle(event, context):
        Adder().handle(event, context)

::

    from cfnlambda import CloudFormationCustomResource
	
	class ExternalServer(CloudFormationCustomResource):
        DISABLE_PHYSICAL_RESOURCE_ID_GENERATION = True # get this from server
        
        def create(self):
            properties:
            response = self.create_server(properties=self.resource_properties)
            if response.status != 200:
                raise Exception('server creation failed')
            self.physical_resource_id = response.hostname
            return {'IP': response.ip}
        
        def update(self):
            response = self.update_server(hostname=self.physical_resource_id, properties=self.resource_properties)
            if response.status != 200:
                raise Exception('server update failed')
            return {'IP': response.ip}
        
        def delete(self):
            response = self.terminate_server(hostname=self.physical_resource_id)
            if response.status != 200:
                raise Exception('server termination failed')
    
    def handle(event, context):
        Adder().handle(event, context)

The :code:`handle` method on :code:`CloudFormationCustomResource` does a few things. It logs
the event and context, populates the class fields, generates a physical resource id
for the resource, and calls the :code:`validate` and :code:`populate` methods that the child class
can override. Then, it calls the :code:`create`, :code:`update`, or :code:`delete` method as 
appropriate, adds any returned dictionary to the :code:`resource_outputs` dict, or, in
case of an exception, sets the status to FAILED. It then cleans up and returns the
result to CloudFormation.

The :code:`resource_outputs` dict is then available in CloudFormation for use with the
:code:`Fn::GetAtt` function.

::

    { "Fn::GetAtt": [ "MyCustomResource", "ip" ] }

If the return value from the :code:`create`/:code:`update`/:code:`delete` method
is not a dict, it is placed into the :code:`resource_outputs` dict with key 'result'.

If the :code:`DELETE_LOGS_ON_STACK_DELETION` class field is set to True, all
CloudWatch logs generated while the stack was created, updated and deleted will
be deleted upon a successful stack deletion. If an exception is thrown during
stack deletion, the logs will always be retained to facilitate troubleshooting.
NOTE: this is not intended for use when multiple stacks access the same function.

Finally, the custom resource will not report a status of FAILED when a stack 
DELETE is attempted. This will prevent a CloudFormation stack from getting stuck
in a DELETE_FAILED state. One side effect of this is that if your AWS Lambda
function throws an exception while trying to process a stack deletion, though 
the stack will show a status of DELETE_COMPLETE, there could still be resources
which your AWS Lambda function created which have not been deleted. This will be
noted in the logs. To disable this feature, set HIDE_STACK_DELETE_FAILURE 
class field to False.

::

    from cfnlambda import handler_decorator

    @handler_decorator(hide_stack_delete_failure=False)
    def lambda_handler(event, context):
        raise Exception(
            'This will result in a CloudFormation stack stuck in a
            DELETE_FAILED state')


How to contribute
-----------------
Feel free to open issues or fork and submit PRs.

* Issue Tracker: https://github.com/benkehoe/cfnlambda/issues
* Source Code: https://github.com/benkehoe/cfnlambda

.. _library of the same name: https://github.com/gene1wood/cfnlambda
.. _AWS CloudFormation custom resources: http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-custom-resources.html
.. _cfn-response: http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-lambda-function-code.html#cfn-lambda-function-code-cfnresponsemodule
