import json


def test_versioned_measurement_contract_example():
    sample = {
        "schema_version": 1,
        "workload": "hash_lookup",
        "iterations": 100000,
        "extra_work": 0,
        "elapsed_ns": 123456,
        "checksum": 42,
    }

    assert sample["schema_version"] == 1
    assert sample["elapsed_ns"] > 0
    assert json.loads(json.dumps(sample)) == sample
