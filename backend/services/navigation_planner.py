from typing import List


class NavigationPlanner:
    """Target-agnostic navigation planner.

    States are generic application lifecycle states (LOGIN, HOME,
    DASHBOARD, LIST, DETAIL, FORM, CONFIRMATION, LOGOUT) resolved via
    ``backend/config/app_states.json``. No vendor-specific pages, URLs,
    or element IDs are assumed here.

    The graph below only encodes universally valid transitions (e.g. any
    authenticated state can return to LOGIN via logout). Anything unknown
    falls back to ``["navigate_direct"]`` so the executor navigates to the
    project's own ``target_url`` instead of following a hardcoded flow.
    """

    def __init__(self, transitions: dict | None = None):
        # Default transitions are intentionally generic. Callers may inject
        # a per-project graph discovered from requirements or a site map.
        self.transitions = transitions if transitions is not None else {
            "LOGIN": {
                "HOME": ["enter_credentials", "click_login"],
                "DASHBOARD": ["enter_credentials", "click_login"],
            },
            "HOME": {
                "LIST": ["navigate_to_list"],
                "FORM": ["open_form"],
                "LOGIN": ["click_logout"],
            },
            "DASHBOARD": {
                "LIST": ["navigate_to_list"],
                "FORM": ["open_form"],
                "LOGIN": ["click_logout"],
            },
            "LIST": {
                "DETAIL": ["select_item"],
                "FORM": ["open_form"],
                "LOGIN": ["click_logout"],
            },
            "DETAIL": {
                "FORM": ["open_form"],
                "LIST": ["go_back_to_list"],
                "LOGIN": ["click_logout"],
            },
            "FORM": {
                "CONFIRMATION": ["fill_form", "submit_form"],
                "LIST": ["cancel_form"],
                "LOGIN": ["click_logout"],
            },
            "CONFIRMATION": {
                "HOME": ["return_home"],
                "LIST": ["return_to_list"],
                "LOGIN": ["click_logout"],
            },
            "LOGOUT": {
                "LOGIN": ["click_login_again"],
            },
        }

    def plan_navigation(self, current_state: str, target_state: str) -> List[str]:
        if current_state == target_state:
            return []

        # Find shortest path using Breadth-First Search (BFS)
        queue = [[current_state]]
        visited = {current_state}

        while queue:
            path = queue.pop(0)
            node = path[-1]

            if node == target_state:
                # Compile the actions needed for the path transitions
                actions = []
                for i in range(len(path) - 1):
                    actions.extend(self.transitions[path[i]][path[i + 1]])
                return actions

            for neighbor in self.transitions.get(node, {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    new_path = list(path)
                    new_path.append(neighbor)
                    queue.append(new_path)

        # Fallback if no path found: force direct navigation / reset
        return ["navigate_direct"]
