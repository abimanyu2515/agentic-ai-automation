"""
Shared vocabulary across models. Priority means the same thing whether
it's on a Scenario or a TestCase - one enum, not one per model.
"""

from enum import Enum


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"