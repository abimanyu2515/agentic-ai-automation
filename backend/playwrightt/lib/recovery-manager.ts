export type RecoveryAction = 'retry' | 'restart_browser' | 're_login' | 'fail';

export class RecoveryManager {
  getRecoveryAction(errorMessage: string, currentRetryCount: number): { action: RecoveryAction; nextRetryCount: number } {
    if (!errorMessage) {
      return { action: 'fail', nextRetryCount: currentRetryCount };
    }

    const errLower = errorMessage.toLowerCase();

    // 1. Assertion failure
    if (errLower.includes('expect') || errLower.includes('assertion') || errLower.includes('assert')) {
      return { action: 'fail', nextRetryCount: currentRetryCount };
    }

    // 2. Browser crash / disconnected
    if (errLower.includes('crash') || errLower.includes('session closed') || errLower.includes('browser closed') || errLower.includes('target closed')) {
      if (currentRetryCount < 1) {
        return { action: 'restart_browser', nextRetryCount: currentRetryCount + 1 };
      }
      return { action: 'fail', nextRetryCount: currentRetryCount };
    }

    // 3. Auth expired
    if (errLower.includes('auth') || errLower.includes('expired') || errLower.includes('login required') || errLower.includes('unauthorized')) {
      if (currentRetryCount < 1) {
        return { action: 're_login', nextRetryCount: currentRetryCount + 1 };
      }
      return { action: 'fail', nextRetryCount: currentRetryCount };
    }

    // 4. Timeout
    if (errLower.includes('timeout') || errLower.includes('timed out') || errLower.includes('waiting for selector') || errLower.includes('waiting for locator')) {
      if (currentRetryCount < 1) {
        return { action: 'retry', nextRetryCount: currentRetryCount + 1 };
      }
      return { action: 'fail', nextRetryCount: currentRetryCount };
    }

    // 5. Network error
    if (errLower.includes('network') || errLower.includes('net::err') || errLower.includes('dns') || errLower.includes('refused') || errLower.includes('navigation failed')) {
      if (currentRetryCount < 2) {
        return { action: 'retry', nextRetryCount: currentRetryCount + 1 };
      }
      return { action: 'fail', nextRetryCount: currentRetryCount };
    }

    // General error
    if (currentRetryCount < 1) {
      return { action: 'retry', nextRetryCount: currentRetryCount + 1 };
    }
    return { action: 'fail', nextRetryCount: currentRetryCount };
  }
}
