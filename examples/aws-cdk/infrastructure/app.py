# examples/aws-cdk/infrastructure/app.py - Synthesize the ECS starter without credentials or bootstrap resources.


import os
from pathlib import Path

from aws_cdk import App, LegacyStackSynthesizer, Environment, Stack
from aws_cdk.cloudformation_include import CfnInclude


# Keep the workload identical so the assembly changes the IaC tool rather than the runtime
class ApplicationStack(Stack):
    # Bind the account and region before synthesis without any cloud lookups
    def __init__(self, app: App):
        super().__init__(app, "ApplicationStack", stack_name="deploy-ops-example",
                         env=Environment(account=os.environ["DEPLOY_OPS_EXAMPLE_ACCOUNT"],
                                         region=os.environ["DEPLOY_OPS_EXAMPLE_REGION"]),
                         synthesizer=LegacyStackSynthesizer())
        CfnInclude(self, "Workload", template_file=str(Path(__file__).with_name("stack.json")),
                   preserve_logical_ids=True)


# Emit one frozen assembly for the trusted deployment worker to inspect
app = App(outdir="cdk.out")
ApplicationStack(app)
app.synth()
