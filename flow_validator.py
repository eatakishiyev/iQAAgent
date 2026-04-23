# ══════════════════════════════════════════════════════════════════
#  VALIDATOR
# ══════════════════════════════════════════════════════════════════
from iQAProject.qa_agent_tools import TOOL_MAP


def validate_flow(flow: dict) -> list[str]:
    errors   = []
    nodes    = flow.get("nodes", [])
    node_ids = {n["id"] for n in nodes}

    if not any(n["type"] == "start" for n in nodes):
        errors.append("No start node found")
    if not any(n["type"] == "end" for n in nodes):
        errors.append("No end node found")

    for n in nodes:
        nid, ntype = n.get("id", "?"), n.get("type", "?")

        if ntype == "action":
            if n.get("tool") not in TOOL_MAP:
                errors.append(f"Node '{nid}': unknown tool '{n.get('tool')}'")
            if n.get("next") not in node_ids:
                errors.append(f"Node '{nid}': 'next' → unknown '{n.get('next')}'")

        elif ntype == "wait":
            if n.get("next") not in node_ids:
                errors.append(f"Node '{nid}': 'next' → unknown '{n.get('next')}'")

        elif ntype == "gateway":
            branches = n.get("branches", [])
            if not branches:
                errors.append(f"Node '{nid}': gateway has no branches")
            if branches and branches[-1].get("condition") != "default":
                errors.append(f"Node '{nid}': last branch must be 'default'")
            for b in branches:
                if b.get("next") not in node_ids:
                    errors.append(f"Node '{nid}': branch → unknown '{b.get('next')}'")

        elif ntype == "start":
            if n.get("next") not in node_ids:
                errors.append(f"Start 'next' → unknown '{n.get('next')}'")

    return errors

