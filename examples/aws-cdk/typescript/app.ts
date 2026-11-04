// examples/aws-cdk/typescript/app.ts - Produce the same frozen stack assembly from TypeScript.


import { App, LegacyStackSynthesizer, Stack } from "aws-cdk-lib";
import { CfnInclude } from "aws-cdk-lib/cloudformation-include";


// Bind an explicit environment without using credentialed context lookups
const account = process.env.DEPLOY_OPS_EXAMPLE_ACCOUNT;
const region = process.env.DEPLOY_OPS_EXAMPLE_REGION;
if (!account || !region) throw new Error("Set the example account and region before synthesis");
const app = new App({ outdir: "cdk.out" });
const stack = new Stack(app, "ApplicationStack", {
  stackName: "deploy-ops-example",
  env: { account, region },
  synthesizer: new LegacyStackSynthesizer(),
});


// Reuse the same workload contract to keep the language choice independent
new CfnInclude(stack, "Workload", {
  templateFile: "stack.json",
  preserveLogicalIds: true,
});
app.synth();
