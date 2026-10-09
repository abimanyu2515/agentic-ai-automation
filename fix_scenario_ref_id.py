"""
Add scenario_ref_id to ALL ScenarioResponse constructions
"""
import re

# Read the file
with open('backend/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern to find ScenarioResponse construction with requirement_id followed by scenario_name
# We need to add scenario_ref_id between them
pattern = r'(requirement_id=(?:s|updated|duplicated)\.requirement_id,)\s*\n(\s+)(scenario_name=)'

# Replacement adds scenario_ref_id line
replacement = r'\1\n\2scenario_ref_id=\g<0>.scenario_ref_id,\n\2\3'

# Actually, let's do a simpler approach - just insert the line
# Find all instances where we have requirement_id followed directly by scenario_name
lines = content.split('\n')
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    new_lines.append(line)
    
    # Check if this line has requirement_id and next line has scenario_name
    if 'requirement_id=' in line and 'requirement_id' in line and i + 1 < len(lines):
        next_line = lines[i + 1]
        if 'scenario_name=' in next_line and 'scenario_ref_id=' not in next_line:
            # Extract the indentation from next_line
            indent = len(next_line) - len(next_line.lstrip())
            # Determine the variable name (s, updated, or duplicated)
            if 'requirement_id=s.requirement_id' in line:
                var_name = 's'
            elif 'requirement_id=updated.requirement_id' in line:
                var_name = 'updated'
            elif 'requirement_id=duplicated.requirement_id' in line:
                var_name = 'duplicated'
            else:
                var_name = 's'  # default
            
            # Insert scenario_ref_id line
            new_lines.append(' ' * indent + f'scenario_ref_id={var_name}.scenario_ref_id,')
    
    i += 1

# Write back
with open('backend/main.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(new_lines))

print("✅ Added scenario_ref_id to all ScenarioResponse constructions")
