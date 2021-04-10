import json
from typing import Callable, Collection, Dict, List, Optional, Sequence, Union

import torch

from ignite.metrics.fbeta import Fbeta
from ignite.metrics.metric import Metric, reinit__is_reduced
from ignite.metrics.metrics_lambda import MetricsLambda
from ignite.metrics.precision import Precision
from ignite.metrics.recall import Recall

__all__ = ["ClassificationReport"]


class ClassificationReport(MetricsLambda):
    """Build a text report showing the main classification metrics. The report resembles in functionality
        'https://scikit-learn.org/stable/modules/generated/sklearn.metrics.classification_report.html#sklearn.metrics.classification_report'
        The underlying implementation doesn't use the sklearn function.

        Args:
            beta: weight of precision in harmonic mean
            output_dict: If True, return output as dict, otherwise return a str
            output_transform: a callable that is used to transform the
                :class:`~ignite.engine.engine.Engine`'s ``process_function``'s output into the
                form expected by the metric. This can be useful if, for example, you have a multi-output model and
                you want to compute the metric with respect to one of the outputs.
            device: optional device specification for internal storage.
            labels: Optional list of label indices to include in the report
            digits: Number of digits for formatting output floating point values, by default it is 5

        .. code-block:: python


            y_true = torch.tensor([[1, 0],
            [0, 1], [1, 0], [1, 1], [1, 0], [0, 1], [0, 1], [1, 0], [0, 1], [1, 0]])
            y_pred = torch.randint(0, 2, size=(10,))

            classification_report = ClassificationReport(output_dict=True, digits=2)
            classification_report.update((y_true, y_pred))
            res = classification_report.compute()

            result should look like this:
            {
                "0": {
                    "precision": 0.33,
                    "recall": 0.5,
                    "f1-score": 0.4
                },
                "1": {
                    "precision": 0.5,
                    "recall": 0.33,
                    "f1-score": 0.4
                },
                "macro avg": {
                    "precision": 0.42,
                    "recall": 0.42,
                    "f1-score": 0.4
                }
            }
    """

    def __init__(
        self,
        beta: int = 1,
        output_dict: bool = False,
        output_transform: Optional[Callable] = None,
        device: Union[str, torch.device] = torch.device("cpu"),
        labels: Optional[List[str]] = None,
    ):
        self.beta = beta
        # setup all the underlying metrics
        self.precision = Precision(
            average=False,
            output_transform=(lambda x: x) if output_transform is None else output_transform,  # type: ignore[arg-type]
            device=device,
        )
        self.recall = Recall(
            average=False,
            output_transform=(lambda x: x) if output_transform is None else output_transform,  # type: ignore[arg-type]
            device=device,
        )
        self.fbeta = Fbeta(beta, average=False, precision=self.precision, recall=self.recall)
        self.averaged_precision = self.precision.mean()
        self.averaged_recall = self.recall.mean()
        self.averaged_fbeta = self.fbeta.mean()

        # setup all output functions
        self.output_dict = output_dict
        self.output_transform = output_transform
        self.labels = labels
        self.combined_metric = MetricsLambda(
            self._wrapper,
            self.recall,
            self.precision,
            self.fbeta,
            self.averaged_recall,
            self.averaged_precision,
            self.averaged_fbeta,
        )
        super(ClassificationReport, self).__init__(
            f=self._wrapper, device=device, precision=self.precision, recall=self.recall
        )

    def _wrapper(
        self, recall: Metric, precision: Metric, f: Metric, a_recall: Metric, a_precision: Metric, a_f: Metric,
    ) -> Union[Collection[str], Dict]:
        p_tensor, r_tensor, f_tensor = precision, recall, f
        assert p_tensor.shape == r_tensor.shape
        dict_obj = {}
        for idx, p_label in enumerate(p_tensor):
            dict_obj[self._get_label_for_class(idx)] = {
                "precision": p_label.item(),
                "recall": r_tensor[idx].item(),
                "f{0}-score".format(self.beta): f_tensor[idx].item(),
            }
        dict_obj["macro avg"] = {
            "precision": a_precision.item(),
            "recall": a_recall.item(),
            "f{0}-score".format(self.beta): a_f.item(),
        }
        return dict_obj if self.output_dict else json.dumps(dict_obj)

    # helper method to get a label for a given class
    def _get_label_for_class(self, idx: int) -> str:
        return self.labels[idx] if self.labels else str(idx)

    @reinit__is_reduced
    def reset(self) -> None:
        self.combined_metric.reset()

    @reinit__is_reduced
    def update(self, output: Sequence[Union[torch.Tensor, Dict]]) -> None:
        # MetricsLambda doesn't update underlying metrics automatically
        # so we have to do this for every underlying metric
        self.precision.update(output)
        self.recall.update(output)
        # self.fbeta.update(output)

    def compute(self) -> Union[Dict, str]:
        return self.combined_metric.compute()
