from typing import Tuple

class RecoveryAction:
    RETRY = "retry"
    RESTART_BROWSER = "restart_browser"
    RE_LOGIN = "re_login"
    FAIL = "fail"

class RecoveryManager:
    def get_recovery_action(self, error_message: str, current_retry_count: int) -> Tuple[str, int]:
        """
        Determines the recovery action and next retry count.
        Returns:
            (action, next_retry_count)
        """
        if not error_message:
            return RecoveryAction.FAIL, current_retry_count

        err_lower = error_message.lower()

        # 1. Assertion failure: 0 retries
        if "expect" in err_lower or "assertion" in err_lower or "assert" in err_lower:
            return RecoveryAction.FAIL, current_retry_count

        # 2. Browser crash / target closed: restart browser & 1 retry max
        if "crash" in err_lower or "session closed" in err_lower or "browser closed" in err_lower or "target closed" in err_lower:
            if current_retry_count < 1:
                return RecoveryAction.RESTART_BROWSER, current_retry_count + 1
            return RecoveryAction.FAIL, current_retry_count

        # 3. Authentication expired: log in again & 1 retry max
        if "auth" in err_lower or "expired" in err_lower or "login required" in err_lower or "unauthorized" in err_lower:
            if current_retry_count < 1:
                return RecoveryAction.RE_LOGIN, current_retry_count + 1
            return RecoveryAction.FAIL, current_retry_count

        # 4. Timeout: 1 retry max
        if "timeout" in err_lower or "timed out" in err_lower or "waiting for selector" in err_lower or "waiting for locator" in err_lower:
            if current_retry_count < 1:
                return RecoveryAction.RETRY, current_retry_count + 1
            return RecoveryAction.FAIL, current_retry_count

        # 5. Network Error: 2 retries max
        if "network" in err_lower or "net::err" in err_lower or "dns" in err_lower or "refused" in err_lower or "navigation failed" in err_lower:
            if current_retry_count < 2:
                return RecoveryAction.RETRY, current_retry_count + 1
            return RecoveryAction.FAIL, current_retry_count

        # General error fallback: 1 retry max
        if current_retry_count < 1:
            return RecoveryAction.RETRY, current_retry_count + 1
        return RecoveryAction.FAIL, current_retry_count
