export class NavigationPlanner {
  private transitions: Record<string, Record<string, string[]>> = {
    LOGIN: {
      SEARCH: ["enter_credentials", "click_login"]
    },
    SEARCH: {
      RESULTS: ["fill_search_fields", "click_search"],
      LOGIN: ["click_logout"]
    },
    RESULTS: {
      BOOKING: ["select_hotel", "click_continue"],
      SEARCH: ["click_back"],
      LOGIN: ["click_logout"]
    },
    BOOKING: {
      CONFIRMATION: ["fill_billing_details", "click_book_now"],
      RESULTS: ["click_cancel"],
      LOGIN: ["click_logout"]
    },
    CONFIRMATION: {
      SEARCH: ["click_search_hotel"],
      LOGIN: ["click_logout"]
    },
    PROFILE: {
      SEARCH: ["click_search_hotel"],
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
