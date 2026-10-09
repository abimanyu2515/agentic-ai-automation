from typing import List, Dict

class NavigationPlanner:
    def __init__(self):
        # Default state transitions graph.
        # Edges denote the transition target, values denote the abstract actions.
        self.transitions = {
            "LOGIN": {
                "SEARCH": ["enter_credentials", "click_login"]
            },
            "SEARCH": {
                "RESULTS": ["fill_search_fields", "click_search"],
                "LOGIN": ["click_logout"]
            },
            "RESULTS": {
                "BOOKING": ["select_hotel", "click_continue"],
                "SEARCH": ["click_back"],
                "LOGIN": ["click_logout"]
            },
            "BOOKING": {
                "CONFIRMATION": ["fill_billing_details", "click_book_now"],
                "RESULTS": ["click_cancel"],
                "LOGIN": ["click_logout"]
            },
            "CONFIRMATION": {
                "SEARCH": ["click_search_hotel"],
                "LOGIN": ["click_logout"]
            },
            "PROFILE": {
                "SEARCH": ["click_search_hotel"],
                "LOGIN": ["click_logout"]
            },
            "LOGOUT": {
                "LOGIN": ["click_login_again"]
            }
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
                    actions.extend(self.transitions[path[i]][path[i+1]])
                return actions

            for neighbor in self.transitions.get(node, {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    new_path = list(path)
                    new_path.append(neighbor)
                    queue.append(new_path)

        # Fallback if no path found: force direct navigation / reset
        return ["navigate_direct"]
