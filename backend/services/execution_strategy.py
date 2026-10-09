from abc import ABC, abstractmethod
from typing import Callable, Optional, Any
from backend.models.batch_context import BatchContext, QueueItem

class ExecutionStrategy(ABC):
    @abstractmethod
    def execute(
        self,
        context: BatchContext,
        runner: Any,
        on_log_callback: Optional[Callable[[str], None]] = None
    ) -> None:
        pass

class SequentialStrategy(ExecutionStrategy):
    def execute(
        self,
        context: BatchContext,
        runner: Any,
        on_log_callback: Optional[Callable[[str], None]] = None
    ) -> None:
        if on_log_callback:
            on_log_callback(f"Starting batch execution using SequentialStrategy. Batch ID: {context.batch_id}")

        for item in context.queue:
            if item.status != "queued":
                continue

            item.status = "running"
            if on_log_callback:
                on_log_callback(f"Running Queue Item: {item.task_id} (TestCase ID: {item.testcase_id})")

            # We will fetch the test case script and run it
            # The execution logic and retry/recovery policy is orchestrated here or delegated to the queue manager.
            # Real execution is done inside BatchQueueManager, which uses the strategy to schedule runs.
            pass
