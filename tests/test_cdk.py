# tests/test_cdk.py - Keep synthesized inputs and asset publication bound to reviewed stack changes.


import json

import pytest

from deploy_ops.delivery.adapters import cdk
from deploy_ops.errors import Refused
from test_cloudformation import ACCOUNT, TEMPLATE, context


# Build a protocol fixture without running application code or a cloud SDK
def assembly(ctx, files=False):
    folder = ctx.source / "cdk.out"
    folder.mkdir()
    (folder / "Stack.template.json").write_text(json.dumps(TEMPLATE))
    artifacts = {"Stack": {"type": "aws:cloudformation:stack", "environment": f"aws://{ACCOUNT}/us-east-1",
                          "properties": {"templateFile": "Stack.template.json", "stackName": "example"}}}
    if files:
        (folder / "asset.txt").write_text("frozen asset\n")
        (folder / "assets.json").write_text(json.dumps({"version": "55.0.0", "files": {
            "asset-hash": {"source": {"path": "asset.txt", "packaging": "file"},
                           "destinations": {"test": {"bucketName": "test-assets", "objectKey": "reviewed/asset.txt"}}}}}))
        artifacts["Assets"] = {"type": "cdk:asset-manifest", "properties": {"file": "assets.json"}}
        artifacts["Stack"]["dependencies"] = ["Assets"]
        ctx.options.update(asset_bucket="test-assets", asset_prefix="reviewed/")
    (folder / "manifest.json").write_text(json.dumps({"version": "55.0.0", "artifacts": artifacts}))
    ctx.options.update(assembly="cdk.out", stack_artifact="Stack")
    ctx.component = ctx.component.model_copy(update={"adapter": "cdk"})
    return folder


# Deliver an asset-free assembly through the native reviewed change set
def test_frozen_assembly_uses_cloudformation(tmp_path):
    ctx = context(tmp_path)
    assembly(ctx)
    prepared = cdk.prepare(ctx)
    assert prepared["cdk"]["schema_version"] == "55.0.0"
    assert cdk.apply(ctx, prepared)["ImageRepository"]
    assert all(call[0] == "aws" for call in ctx.process.calls)
    assert not any("cdk" == part for call in ctx.process.calls for part in call)


# Publish frozen bytes only after approval and retain immutable remote checksums
def test_file_asset_publication_is_exact_and_scoped(tmp_path):
    ctx = context(tmp_path)
    assembly(ctx, files=True)
    prepared = cdk.prepare(ctx)
    assert not ctx.process.objects
    assert prepared["preview"]["changes"][-1]["resource_type"] == "cdk_file_asset"
    cdk.apply(ctx, prepared)
    assert "reviewed/asset.txt" in ctx.process.objects
    put = next(call for call in ctx.process.calls if "put-object" in call)
    assert put[put.index("--expected-bucket-owner") + 1] == ACCOUNT
    assert "--checksum-sha256" in put
    assert ctx.process.calls.index(put) < next(index for index, call in enumerate(ctx.process.calls) if "execute-change-set" in call)


# Stale stack evidence must refuse asset writes as well as deployment
def test_stale_stack_does_not_publish_assets(tmp_path):
    ctx = context(tmp_path)
    assembly(ctx, files=True)
    prepared = cdk.prepare(ctx)
    ctx.process.current["LastUpdatedTime"] = "outside-change"
    with pytest.raises(Refused, match="changed"):
        cdk.apply(ctx, prepared)
    assert not ctx.process.objects


# Refuse unsupported semantics before even creating a change set
@pytest.mark.parametrize("mutation", ["future", "lookup", "role", "target", "dependencies", "executable", "destination"])
def test_unsupported_assembly_instructions_fail_closed(tmp_path, mutation):
    ctx = context(tmp_path)
    folder = assembly(ctx, files=mutation in {"executable", "destination"})
    value = json.loads((folder / "manifest.json").read_text())
    selected = value["artifacts"]["Stack"]
    if mutation == "future":
        value["version"] = "999.0.0"
    elif mutation == "lookup":
        value["missing"] = [{"key": "lookup"}]
    elif mutation == "role":
        selected["properties"]["assumeRoleArn"] = "arn:aws:iam::123456789012:role/undeclared"
    elif mutation == "target":
        selected["environment"] = "aws://999999999999/us-east-1"
    elif mutation == "dependencies":
        selected["dependencies"] = ["UnknownStack"]
    elif mutation == "executable":
        asset = json.loads((folder / "assets.json").read_text())
        asset["files"]["asset-hash"]["source"] = {"executable": ["unsafe-command"]}
        (folder / "assets.json").write_text(json.dumps(asset))
    else:
        ctx.options["asset_bucket"] = "different-bucket"
    (folder / "manifest.json").write_text(json.dumps(value))
    with pytest.raises(Refused):
        cdk.prepare(ctx)
    assert not any("create-change-set" in call for call in ctx.process.calls)


# Refuse modified synthesis outputs even when the source application is untouched
def test_assembly_tampering_refuses_execution(tmp_path):
    ctx = context(tmp_path)
    assembly(ctx)
    prepared = cdk.prepare(ctx)
    (ctx.stage / "cdk-assembly" / "Stack.template.json").write_text("{}")
    with pytest.raises(Refused, match="assembly changed"):
        cdk.apply(ctx, prepared)
    assert not any("execute-change-set" in call for call in ctx.process.calls)


# Packaging is deterministic and never follows symbolic links
def test_zip_assets_retain_reproducible_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.txt").write_text("example\n")
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    cdk.freeze_asset(source, first, "zip")
    cdk.freeze_asset(source, second, "zip")
    assert first.read_bytes() == second.read_bytes()
    (source / "link").symlink_to(source / "index.txt")
    with pytest.raises(Refused, match="symbolic links"):
        cdk.freeze_asset(source, second, "zip")


# A conflicting remote asset is never replaced to make a deployment pass
def test_existing_asset_checksum_conflict_refuses_execution(tmp_path):
    ctx = context(tmp_path)
    assembly(ctx, files=True)
    prepared = cdk.prepare(ctx)
    ctx.process.objects["reviewed/asset.txt"] = "different-checksum"
    with pytest.raises(Refused, match="checksum"):
        cdk.apply(ctx, prepared)
    assert not any("put-object" in call or "execute-change-set" in call for call in ctx.process.calls)
