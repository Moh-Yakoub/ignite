import os

import pytest
import torch

import ignite.distributed as idist
from ignite.engine import Engine
from ignite.metrics import EpochMetric
from ignite.metrics.epoch_metric import EpochMetricWarning, NotComputableError, _is_scalar_or_collection_of_tensor


def test_epoch_metric_wrong_setup_or_input():

    # Wrong compute function
    with pytest.raises(TypeError, match=r"Argument compute_fn should be callable."):
        EpochMetric(12345)

    def compute_fn(y_preds, y_targets):
        return 0.0

    em = EpochMetric(compute_fn)

    # Wrong input dims
    with pytest.raises(ValueError, match=r"Predictions should be of shape"):
        output = (torch.tensor(0), torch.tensor(0))
        em.update(output)

    # Wrong input dims
    with pytest.raises(ValueError, match=r"Targets should be of shape"):
        output = (torch.rand(4, 3), torch.rand(4, 3, 1))
        em.update(output)

    # Wrong input dims
    with pytest.raises(ValueError, match=r"Predictions should be of shape"):
        output = (torch.rand(4, 3, 1), torch.rand(4, 3))
        em.update(output)

    em.reset()
    output1 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output1)

    with pytest.raises(ValueError, match=r"Incoherent types between input y_pred and stored predictions"):
        output2 = (torch.randint(0, 5, size=(4, 3)), torch.randint(0, 2, size=(4, 3)))
        em.update(output2)

    with pytest.raises(ValueError, match=r"Incoherent types between input y and stored targets"):
        output2 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3)).to(torch.int32))
        em.update(output2)

    with pytest.raises(
        NotComputableError, match="EpochMetric must have at least one example before it can be computed"
    ):
        em = EpochMetric(compute_fn)
        em.compute()


def test_epoch_metric():
    def compute_fn(y_preds, y_targets):
        return 0.0

    em = EpochMetric(compute_fn)

    em.reset()
    output1 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output1)
    output2 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output2)

    assert all([t.device.type == "cpu" for t in em._predictions + em._targets])
    assert torch.equal(em._predictions[0], output1[0])
    assert torch.equal(em._predictions[1], output2[0])
    assert torch.equal(em._targets[0], output1[1])
    assert torch.equal(em._targets[1], output2[1])
    assert em.compute() == 0.0

    # test when y and y_pred are (batch_size, 1) that are squeezed to (batch_size, )
    em.reset()
    output1 = (torch.rand(4, 1), torch.randint(0, 2, size=(4, 1), dtype=torch.long))
    em.update(output1)
    output2 = (torch.rand(4, 1), torch.randint(0, 2, size=(4, 1), dtype=torch.long))
    em.update(output2)

    assert all([t.device.type == "cpu" for t in em._predictions + em._targets])
    assert torch.equal(em._predictions[0], output1[0][:, 0])
    assert torch.equal(em._predictions[1], output2[0][:, 0])
    assert torch.equal(em._targets[0], output1[1][:, 0])
    assert torch.equal(em._targets[1], output2[1][:, 0])
    assert em.compute() == 0.0


def test_mse_epoch_metric():
    def compute_fn(y_preds, y_targets):
        return torch.mean(((y_preds - y_targets.type_as(y_preds)) ** 2)).item()

    em = EpochMetric(compute_fn)

    em.reset()
    output1 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output1)
    output2 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output2)
    output3 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output3)

    preds = torch.cat([output1[0], output2[0], output3[0]], dim=0)
    targets = torch.cat([output1[1], output2[1], output3[1]], dim=0)

    result = em.compute()
    assert result == compute_fn(preds, targets)

    em.reset()
    output1 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output1)
    output2 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output2)
    output3 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    em.update(output3)

    preds = torch.cat([output1[0], output2[0], output3[0]], dim=0)
    targets = torch.cat([output1[1], output2[1], output3[1]], dim=0)

    result = em.compute()
    assert result == compute_fn(preds, targets)


def test_bad_compute_fn():
    def compute_fn(y_preds, y_targets):
        # Following will raise the error:
        # The size of tensor a (3) must match the size of tensor b (4)
        # at non-singleton dimension 1
        return torch.mean(y_preds - y_targets).item()

    em = EpochMetric(compute_fn)

    em.reset()
    output1 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 4), dtype=torch.long))
    with pytest.warns(EpochMetricWarning, match=r"Probably, there can be a problem with `compute_fn`"):
        em.update(output1)


def test_check_compute_fn():
    def compute_fn(y_preds, y_targets):
        raise Exception

    em = EpochMetric(compute_fn, check_compute_fn=True)

    em.reset()
    output1 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
    with pytest.warns(EpochMetricWarning, match=r"Probably, there can be a problem with `compute_fn`"):
        em.update(output1)

    em = EpochMetric(compute_fn, check_compute_fn=False)
    em.update(output1)


def _test_distrib_integration(device=None):

    if device is None:
        device = idist.device() if idist.device().type != "xla" else "cpu"

    rank = idist.get_rank()
    torch.manual_seed(12)

    n_iters = 60
    s = 16
    n_classes = 7

    offset = n_iters * s
    y_true = torch.randint(0, n_classes, size=(offset * idist.get_world_size(),), device=device)
    y_preds = torch.rand(offset * idist.get_world_size(), n_classes, device=device)

    def update(engine, i):
        return (
            y_preds[i * s + rank * offset : (i + 1) * s + rank * offset, :],
            y_true[i * s + rank * offset : (i + 1) * s + rank * offset],
        )

    engine = Engine(update)

    def assert_data_fn(all_preds, all_targets):
        assert all_preds.equal(y_preds), f"{all_preds.shape} vs {y_preds.shape}"
        assert all_targets.equal(y_true), f"{all_targets.shape} vs {y_true.shape}"
        return (all_preds.argmax(dim=1) == all_targets).sum().item()

    ep_metric = EpochMetric(assert_data_fn, check_compute_fn=False, device=device)
    ep_metric.attach(engine, "epm")

    data = list(range(n_iters))
    engine.run(data=data, max_epochs=3)
    assert engine.state.metrics["epm"] == (y_preds.argmax(dim=1) == y_true).sum().item()

    ep_metric.reset()

    def compute_fn_sequence(all_preds, all_targets):
        return [
            torch.tensor((all_preds.argmax(dim=1) == all_targets).sum().item()),
            torch.tensor((all_preds.argmin(dim=1) == all_targets).sum().item()),
        ]

    ep_metric = EpochMetric(compute_fn_sequence, check_compute_fn=False, device=device)
    ep_metric.attach(engine, "epm")

    data = list(range(n_iters))
    engine.run(data=data, max_epochs=3)
    assert engine.state.metrics["epm"] == [
        torch.tensor((y_preds.argmax(dim=1) == y_true).sum().item()),
        torch.tensor((y_preds.argmin(dim=1) == y_true).sum().item()),
    ]

    def compute_fn_mapping(all_preds, all_targets):
        return {
            "first": torch.tensor((all_preds.argmax(dim=1) == all_targets).sum().item()),
            "second": torch.tensor((all_preds.argmin(dim=1) == all_targets).sum().item()),
        }

    ep_metric = EpochMetric(compute_fn_mapping, check_compute_fn=False, device=device)
    ep_metric.attach(engine, "epm")

    data = list(range(n_iters))
    engine.run(data=data, max_epochs=3)
    assert engine.state.metrics["epm"] == {
        "first": torch.tensor((y_preds.argmax(dim=1) == y_true).sum().item()),
        "second": torch.tensor((y_preds.argmin(dim=1) == y_true).sum().item()),
    }

    def compute_fn_tuple(all_preds, all_targets):
        return (
            torch.tensor((all_preds.argmax(dim=1) == all_targets).sum().item()),
            torch.tensor((all_preds.argmin(dim=1) == all_targets).sum().item()),
        )

    ep_metric = EpochMetric(compute_fn_tuple, check_compute_fn=False, device=device)
    ep_metric.attach(engine, "epm")

    data = list(range(n_iters))
    engine.run(data=data, max_epochs=3)
    assert engine.state.metrics["epm"] == (
        torch.tensor((y_preds.argmax(dim=1) == y_true).sum().item()),
        torch.tensor((y_preds.argmin(dim=1) == y_true).sum().item()),
    )


def test_is_scalar_or_collection_of_tensor():
    def _test(input, expected_val):
        assert _is_scalar_or_collection_of_tensor(input) == expected_val

    _test(4, True)
    _test(4.0, True)
    _test(torch.tensor([1, 2, 3]), True)
    _test([1, 2, 3], False)
    _test("val", False)
    _test([torch.tensor([1, 2, 3]), torch.tensor([5, 6])], True)
    _test([torch.tensor([1, 2, 3]), 3], False)
    _test({"key": "val"}, False)
    _test({"key": torch.tensor([1, 2, 3])}, True)
    _test({"key": torch.tensor([1, 2, 3]), "key2": "val"}, False)
    _test((torch.tensor([1, 3, 4]), torch.tensor([2, 3, 4])), True)
    _test((1, 3), False)
    _test((1, torch.tensor([2, 3, 4])), False)


@pytest.mark.parametrize("input", ["wrongval", [1, 2, "wrongval"], {1: "welcome"}, (1, "welcome")])
def test_epoch_metric_wrong_compute_fn_return(input):
    def compute_fn(y_preds, y_targets):
        return input

    with pytest.raises(
        TypeError, match=r"output not supported: compute_fn should return scalar, tensor, tuple/list/mapping of tensors"
    ):
        em = EpochMetric(compute_fn)
        output1 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
        em.update(output1)
        output2 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
        em.update(output2)
        output3 = (torch.rand(4, 3), torch.randint(0, 2, size=(4, 3), dtype=torch.long))
        em.update(output3)
        em.compute()


@pytest.mark.distributed
@pytest.mark.skipif(not idist.has_native_dist_support, reason="Skip if no native dist support")
@pytest.mark.skipif(torch.cuda.device_count() < 1, reason="Skip if no GPU")
def test_distrib_gpu(distributed_context_single_node_nccl):
    _test_distrib_integration(device="cuda")


@pytest.mark.distributed
@pytest.mark.skipif(not idist.has_native_dist_support, reason="Skip if no native dist support")
def test_distrib_cpu(distributed_context_single_node_gloo):
    _test_distrib_integration(device="cpu")


@pytest.mark.tpu
@pytest.mark.skipif("NUM_TPU_WORKERS" in os.environ, reason="Skip if NUM_TPU_WORKERS is in env vars")
@pytest.mark.skipif(not idist.has_xla_support, reason="Skip if no PyTorch XLA package")
def test_distrib_single_device_xla():
    _test_distrib_integration()


def _test_distrib_xla_nprocs(index):
    _test_distrib_integration()


@pytest.mark.tpu
@pytest.mark.skipif("NUM_TPU_WORKERS" not in os.environ, reason="Skip if no NUM_TPU_WORKERS in env vars")
@pytest.mark.skipif(not idist.has_xla_support, reason="Skip if no PyTorch XLA package")
def test_distrib_xla_nprocs(xmp_executor):
    n = int(os.environ["NUM_TPU_WORKERS"])
    xmp_executor(_test_distrib_xla_nprocs, args=(), nprocs=n)


@pytest.mark.distributed
@pytest.mark.skipif(not idist.has_hvd_support, reason="Skip if no Horovod dist support")
@pytest.mark.skipif("WORLD_SIZE" in os.environ, reason="Skip if launched as multiproc")
def test_distrib_hvd(gloo_hvd_executor):

    nproc = 4 if not torch.cuda.is_available() else torch.cuda.device_count()

    gloo_hvd_executor(_test_distrib_integration, (None,), np=nproc, do_init=True)
