export class NavigationPlanner {
  // Target-agnostic transitions over generic lifecycle states
  // (LOGIN, HOME, DASHBOARD, LIST, DETAIL, FORM, CONFIRMATION, LOGOUT).
  // Unknown state pairs fall back to direct navigation to the project's
  // own target_url instead of a hardcoded vendor flow.
  private transitions: Record<string, Record<string, string[]>> = {
    LOGIN: {
      HOME: ["enter_credentials", "click_login"],
      DASHBOARD: ["enter_credentials", "click_login"]
    },
    HOME: {
      LIST: ["navigate_to_list"],
      FORM: ["open_form"],
      LOGIN: ["click_logout"]
    },
    DASHBOARD: {
      LIST: ["navigate_to_list"],
      FORM: ["open_form"],
      LOGIN: ["click_logout"]
    },
    LIST: {
      DETAIL: ["select_item"],
      FORM: ["open_form"],
      LOGIN: ["click_logout"]
    },
    DETAIL: {
      FORM: ["open_form"],
      LIST: ["go_back_to_list"],
      LOGIN: ["click_logout"]
    },
    FORM: {
      CONFIRMATION: ["fill_form", "submit_form"],
      LIST: ["cancel_form"],
      LOGIN: ["click_logout"]
    },
    CONFIRMATION: {
      HOME: ["return_home"],
      LIST: ["return_to_list"],
      LOGIN: ["click_logout"]
    },
    LOGOUT: {
      LOGIN: ["click_login_again"]
    }
  };

  planNavigation(currentState: string, targetState: string): string[] {
    if (currentState === targetState) return [];

    const queue: string[][] = [[currentState]];
    const visited = new Set<string>([currentState]);

    while (queue.length > 0) {
      const path = queue.shift()!;
      const node = path[path.length - 1];

      if (node === targetState) {
        const actions: string[] = [];
        for (let i = 0; i < path.length - 1; i++) {
          const stepActions = this.transitions[path[i]]?.[path[i + 1]] || [];
          actions.push(...stepActions);
        }
        return actions;
      }

      const neighbors = Object.keys(this.transitions[node] || {});
      for (const neighbor of neighbors) {
        if (!visited.has(neighbor)) {
          visited.add(neighbor);
          const newPath = [...path, neighbor];
          queue.push(newPath);
        }
      }
    }

    return ["navigate_direct"];
  }
}
