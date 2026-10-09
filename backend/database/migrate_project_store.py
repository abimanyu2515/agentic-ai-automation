import json
from pathlib import Path

STORE_PATH = Path(__file__).parent / "project_store.json"

def migrate():
    if not STORE_PATH.exists():
        print("project_store.json not found")
        return
    
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. Deduplicate/Populate requirement_id per project
    projects = data.get("projects", [])
    requirements = data.get("requirements", {})
    
    for proj in projects:
        seen_req_ids = set()
        p_name = proj.get("name")
        p_req_uuids = proj.get("requirements", [])
        
        for index, r_uuid in enumerate(p_req_uuids):
            r = requirements.get(r_uuid)
            if not r:
                continue
            
            # If requirement_id is empty, set it
            req_id = r.get("requirement_id")
            if not req_id:
                req_id = f"REQ-{index+1:03d}"
                r["requirement_id"] = req_id
                
            # If already seen in this project, deduplicate it
            if req_id in seen_req_ids:
                new_req_id = f"{req_id}-{index+1}"
                print(f"Deduplicating requirement_id in project '{p_name}': {req_id} -> {new_req_id}")
                r["requirement_id"] = new_req_id
                req_id = new_req_id
                
            seen_req_ids.add(req_id)

    # 2. Text search-and-replace replacements
    content = json.dumps(data, indent=2, ensure_ascii=False)
    
    replacements = {
        "https://sampleapp.tricentis.com/101/app.php": "https://adactinhotelapp.com/",
        "sampleapp.tricentis.com": "adactinhotelapp.com",
        "Tricentis Vehicle Insurance": "Adactin Hotel Booking",
        "Vehicle Insurance Portal": "Adactin Hotel Portal",
        "Vehicle Insurance": "Adactin Hotel Booking",
        "Tricentis Portal": "Adactin Portal",
        "Tricentis": "Adactin",
        "Enter Vehicle Data": "Login and Search Hotel",
        "Vehicle Data": "Search Hotel Parameters",
        "Insurant Data": "Select Hotel Results",
        "Insurant": "Select Hotel",
        "entervehicledata": "login_form",
        "nextenterinsurantdata": "Submit",
        "#make": "#location",
        "#model": "#hotels",
        "#cylindercapacity": "#room_type",
        "#engineperformance": "#room_nos",
        "#dateofmanufacture": "#datepick_in",
        "#numberofseatsmotorcycle": "#adult_room",
        "#numberofseats": "#adult_room",
        "#fuel": "#child_room",
        "BMW": "Sydney",
        "Scooter": "Hotel Creek",
        "Petrol": "0 - None",
        "Audi": "Sydney",
        "Vehicle": "Hotel",
    }

    for src, dst in replacements.items():
        content = content.replace(src, dst)

    with open(STORE_PATH, "w", encoding="utf-8") as f:
        f.write(content)
        
    print("project_store.json successfully migrated and deduplicated.")

if __name__ == "__main__":
    migrate()
