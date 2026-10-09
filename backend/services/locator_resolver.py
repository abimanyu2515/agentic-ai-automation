"""
Locator Resolver Service

Implements a hierarchical fallback strategy for Playwright locator resolution.
This service is invoked ONLY when the primary locator fails, ensuring complete
backward compatibility with existing test execution.

Fallback Hierarchy:
    1. Primary Locator (existing)
    2. Semantic Labels (getByLabel, getByPlaceholder, getByText, getByAltText)
    3. Accessibility (ARIA) (getByRole, aria-label, role attributes)
    4. DOM Attributes (id, name, data-testid, type, class, CSS selector, XPath)
    5. Relative DOM (sibling, parent, form hierarchy)
    6. Bounding Box Coordinates (mouse click at center point)

Usage:
    from backend.services.locator_resolver import LocatorResolver
    
    resolver = LocatorResolver()
    locator_code = resolver.resolve(
        page_variable="page",
        primary_locator="page.locator('#nonexistent')",
        context_info={
            'label': 'Username',
            'placeholder': 'Enter username',
            'nearby_text': 'Login Form'
        }
    )
"""

from typing import Dict, List, Optional, Tuple
from enum import Enum


class LocatorStrategy(Enum):
    """Enumeration of all locator strategies in fallback order"""
    PRIMARY = "Primary Locator"
    SEMANTIC = "Semantic Labels"
    ARIA = "Accessibility (ARIA)"
    DOM_ATTRIBUTES = "DOM Attributes"
    RELATIVE_DOM = "Relative DOM"
    COORDINATES = "Bounding Box Coordinates"


class LocatorResolver:
    """
    Hierarchical locator fallback resolver for Playwright test execution.
    
    This service generates fallback locator strategies when the primary locator fails.
    It does NOT execute locators - it generates Playwright code that implements
    the fallback chain.
    """
    
    def __init__(self):
        """Initialize the locator resolver with an empty cache"""
        self._locator_cache: Dict[str, str] = {}
    
    def resolve(
        self,
        page_variable: str = "page",
        primary_locator: str = None,
        context_info: Optional[Dict[str, any]] = None,
        action: str = "click",
        cache_key: Optional[str] = None
    ) -> str:
        """
        Generate a Playwright code snippet that implements the hierarchical fallback strategy.
        
        Args:
            page_variable: The Page object variable name (default: "page")
            primary_locator: The original locator expression (e.g., "page.locator('#username')")
            context_info: Dictionary containing contextual information about the element:
                - label: String label text
                - placeholder: Placeholder text
                - text: Visible text content
                - alt_text: Alt text for images
                - role: ARIA role
                - aria_label: ARIA label
                - attributes: Dict of DOM attributes (id, name, data-testid, etc.)
                - nearby_text: Text of nearby elements
                - parent_type: Type of parent element (form, div, etc.)
            action: The action to perform (click, fill, select, etc.)
            cache_key: Optional cache key for performance optimization
        
        Returns:
            String containing Playwright TypeScript code that implements the fallback chain
        """
        # Check cache first
        if cache_key and cache_key in self._locator_cache:
            return self._locator_cache[cache_key]
        
        context_info = context_info or {}
        
        # Generate the fallback chain code
        fallback_code = self._generate_fallback_chain(
            page_variable=page_variable,
            primary_locator=primary_locator,
            context_info=context_info,
            action=action
        )
        
        # Cache the result
        if cache_key:
            self._locator_cache[cache_key] = fallback_code
        
        return fallback_code
    
    def _generate_fallback_chain(
        self,
        page_variable: str,
        primary_locator: Optional[str],
        context_info: Dict,
        action: str
    ) -> str:
        """
        Generate the complete fallback chain as a Playwright code snippet.
        
        This generates a try-catch cascade that attempts each strategy in order.
        """
        strategies = []
        
        # Strategy 1: Primary Locator (if provided)
        if primary_locator:
            strategies.append(self._generate_primary_strategy(primary_locator, action))
        
        # Strategy 2: Semantic Labels
        semantic_locators = self._generate_semantic_locators(page_variable, context_info)
        if semantic_locators:
            strategies.append(self._generate_semantic_strategy(semantic_locators, action))
        
        # Strategy 3: ARIA/Accessibility
        aria_locators = self._generate_aria_locators(page_variable, context_info)
        if aria_locators:
            strategies.append(self._generate_aria_strategy(aria_locators, action))
        
        # Strategy 4: DOM Attributes
        dom_locators = self._generate_dom_locators(page_variable, context_info)
        if dom_locators:
            strategies.append(self._generate_dom_strategy(dom_locators, action))
        
        # Strategy 5: Relative DOM
        relative_locators = self._generate_relative_locators(page_variable, context_info)
        if relative_locators:
            strategies.append(self._generate_relative_strategy(relative_locators, action))
        
        # Strategy 6: Bounding Box Coordinates (final fallback)
        if context_info.get('coordinates'):
            strategies.append(self._generate_coordinates_strategy(page_variable, context_info['coordinates']))
        
        # Build the complete try-catch chain
        return self._build_try_catch_chain(strategies)
    
    def _generate_primary_strategy(self, primary_locator: str, action: str) -> Dict:
        """Generate code for the primary locator strategy"""
        return {
            'name': LocatorStrategy.PRIMARY.value,
            'code': f"await {primary_locator}.{action}();"
        }
    
    def _generate_semantic_locators(self, page_var: str, context: Dict) -> List[str]:
        """Generate semantic locator expressions (Level 1)"""
        locators = []
        
        if context.get('label'):
            locators.append(f"{page_var}.getByLabel('{self._escape_string(context['label'])}')")
        
        if context.get('placeholder'):
            locators.append(f"{page_var}.getByPlaceholder('{self._escape_string(context['placeholder'])}')")
        
        if context.get('text'):
            locators.append(f"{page_var}.getByText('{self._escape_string(context['text'])}')")
        
        if context.get('alt_text'):
            locators.append(f"{page_var}.getByAltText('{self._escape_string(context['alt_text'])}')")
        
        return locators
    
    def _generate_semantic_strategy(self, locators: List[str], action: str) -> Dict:
        """Generate fallback code for semantic locators"""
        attempts = "\n      ".join([f"await {loc}.{action}();" for loc in locators])
        return {
            'name': LocatorStrategy.SEMANTIC.value,
            'code': attempts
        }
    
    def _generate_aria_locators(self, page_var: str, context: Dict) -> List[str]:
        """Generate ARIA/accessibility locator expressions (Level 2)"""
        locators = []
        
        if context.get('role'):
            role = context['role']
            if context.get('aria_label'):
                locators.append(f"{page_var}.getByRole('{role}', {{ name: '{self._escape_string(context['aria_label'])}' }})")
            else:
                locators.append(f"{page_var}.getByRole('{role}')")
        
        if context.get('aria_label'):
            locators.append(f"{page_var}.locator('[aria-label=\"{self._escape_string(context['aria_label'])}\"]')")
        
        if context.get('aria_labelledby'):
            locators.append(f"{page_var}.locator('[aria-labelledby=\"{self._escape_string(context['aria_labelledby'])}\"]')")
        
        return locators
    
    def _generate_aria_strategy(self, locators: List[str], action: str) -> Dict:
        """Generate fallback code for ARIA locators"""
        attempts = "\n      ".join([f"await {loc}.{action}();" for loc in locators])
        return {
            'name': LocatorStrategy.ARIA.value,
            'code': attempts
        }
    
    def _generate_dom_locators(self, page_var: str, context: Dict) -> List[str]:
        """Generate DOM attribute locator expressions (Level 3)"""
        locators = []
        attributes = context.get('attributes', {})
        
        # Priority order: id, name, data-testid, data-test, type, class
        priority_attrs = ['id', 'name', 'data-testid', 'data-test', 'type', 'class']
        
        for attr in priority_attrs:
            if attr in attributes:
                value = attributes[attr]
                if attr == 'id':
                    locators.append(f"{page_var}.locator('#{value}')")
                elif attr == 'class':
                    # Use first class only to avoid specificity issues
                    first_class = value.split()[0] if value else value
                    locators.append(f"{page_var}.locator('.{first_class}')")
                else:
                    locators.append(f"{page_var}.locator('[{attr}=\"{self._escape_string(value)}\"]')")
        
        # CSS selector fallback
        if context.get('css_selector'):
            locators.append(f"{page_var}.locator('{self._escape_string(context['css_selector'])}')")
        
        # XPath as final DOM attribute strategy
        if context.get('xpath'):
            locators.append(f"{page_var}.locator('{self._escape_string(context['xpath'])}')")
        
        return locators
    
    def _generate_dom_strategy(self, locators: List[str], action: str) -> Dict:
        """Generate fallback code for DOM attribute locators"""
        attempts = "\n      ".join([f"await {loc}.{action}();" for loc in locators])
        return {
            'name': LocatorStrategy.DOM_ATTRIBUTES.value,
            'code': attempts
        }
    
    def _generate_relative_locators(self, page_var: str, context: Dict) -> List[str]:
        """Generate relative DOM locator expressions (Level 4)"""
        locators = []
        
        # Form hierarchy (for input fields inside forms)
        if context.get('parent_type') == 'form' and context.get('parent_id'):
            parent_id = context['parent_id']
            if context.get('attributes', {}).get('name'):
                name = context['attributes']['name']
                locators.append(f"{page_var}.locator('form#{parent_id} [name=\"{name}\"]')")
        
        # Sibling-based location
        if context.get('nearby_text'):
            nearby = self._escape_string(context['nearby_text'])
            locators.append(f"{page_var}.locator(`text={nearby}`).locator('xpath=following-sibling::*[1]')")
            locators.append(f"{page_var}.locator(`text={nearby}`).locator('xpath=preceding-sibling::*[1]')")
        
        # Parent container search
        if context.get('parent_class'):
            parent_class = context['parent_class']
            if context.get('element_type'):
                elem_type = context['element_type']
                locators.append(f"{page_var}.locator('.{parent_class} {elem_type}').first()")
        
        return locators
    
    def _generate_relative_strategy(self, locators: List[str], action: str) -> Dict:
        """Generate fallback code for relative DOM locators"""
        attempts = "\n      ".join([f"await {loc}.{action}();" for loc in locators])
        return {
            'name': LocatorStrategy.RELATIVE_DOM.value,
            'code': attempts
        }
    
    def _generate_coordinates_strategy(self, page_var: str, coordinates: Dict) -> Dict:
        """Generate bounding box coordinate click strategy (Level 5 - final fallback)"""
        x = coordinates.get('x', 0)
        y = coordinates.get('y', 0)
        width = coordinates.get('width', 0)
        height = coordinates.get('height', 0)
        
        # Calculate center point
        center_x = x + (width / 2)
        center_y = y + (height / 2)
        
        code = f"await {page_var}.mouse.click({center_x}, {center_y});"
        
        return {
            'name': LocatorStrategy.COORDINATES.value,
            'code': code
        }
    
    def _build_try_catch_chain(self, strategies: List[Dict]) -> str:
        """
        Build the complete try-catch cascade for all strategies.
        
        Each strategy is wrapped in a try-catch that logs the attempt and
        falls through to the next strategy if it fails.
        """
        if not strategies:
            return "throw new Error('[FALLBACK] No locator strategies available');"
        
        # Build nested try-catch blocks
        code_lines = []
        indent = ""
        
        for i, strategy in enumerate(strategies):
            is_last = (i == len(strategies) - 1)
            
            code_lines.append(f"{indent}// [FALLBACK] Attempting: {strategy['name']}")
            code_lines.append(f"{indent}try {{")
            code_lines.append(f"{indent}  console.log('[FALLBACK] {strategy['name']} - Attempting...');")
            code_lines.append(f"{indent}  {strategy['code']}")
            code_lines.append(f"{indent}  console.log('[FALLBACK] {strategy['name']} - SUCCESS');")
            
            if not is_last:
                code_lines.append(f"{indent}}} catch (error{i}) {{")
                code_lines.append(f"{indent}  console.log('[FALLBACK] {strategy['name']} - FAILED');")
                indent += "  "
            else:
                # Last strategy - if this fails, throw
                code_lines.append(f"{indent}}} catch (error{i}) {{")
                code_lines.append(f"{indent}  console.log('[FALLBACK] {strategy['name']} - FAILED');")
                code_lines.append(f"{indent}  console.log('[FALLBACK] All strategies exhausted');")
                code_lines.append(f"{indent}  throw new Error('[FALLBACK] All locator strategies failed: ' + error{i}.message);")
                code_lines.append(f"{indent}}}")
        
        # Close all open try-catch blocks (except the last one which we already closed)
        for i in range(len(strategies) - 1):
            indent = indent[:-2]  # Remove 2 spaces
            code_lines.append(f"{indent}}}")
        
        return "\n".join(code_lines)
    
    def _escape_string(self, value: str) -> str:
        """Escape string for use in Playwright locators"""
        if not value:
            return ""
        return value.replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")
    
    def clear_cache(self):
        """Clear the locator cache (useful for testing or between test runs)"""
        self._locator_cache.clear()
    
    def generate_fallback_wrapper(
        self,
        original_action_code: str,
        context_info: Optional[Dict] = None,
        page_variable: str = "page"
    ) -> str:
        """
        Wrap an existing action code with fallback logic.
        
        This is a convenience method that wraps a simple action line with
        the complete fallback chain.
        
        Args:
            original_action_code: The original Playwright action (e.g., "await page.locator('#btn').click();")
            context_info: Context information for fallback strategies
            page_variable: The Page object variable name
        
        Returns:
            Complete code with fallback chain
        """
        # Extract action type from the original code
        action = "click"
        if ".fill(" in original_action_code:
            action = "fill"
        elif ".selectOption(" in original_action_code:
            action = "selectOption"
        elif ".check(" in original_action_code:
            action = "check"
        
        return self.resolve(
            page_variable=page_variable,
            primary_locator=original_action_code.replace(f"await ", "").replace(f".{action}();", ""),
            context_info=context_info,
            action=action
        )


# Singleton instance
_locator_resolver = None


def get_locator_resolver() -> LocatorResolver:
    """Get or create singleton instance of LocatorResolver"""
    global _locator_resolver
    if _locator_resolver is None:
        _locator_resolver = LocatorResolver()
    return _locator_resolver
