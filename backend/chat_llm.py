"""
Azure AI Foundry chat helper for the Ontwerp page.

The chat assistant lets the user describe changes to the design's flows in
natural language. It owns one tool, ``set_flows``, which replaces the entire
``flows`` list of the current design — the Sankey at the top of the page is
already a render of that list, so applying the tool re-draws the diagram.

The current design is always passed in the system prompt so the model knows
the active state without the user having to re-state it.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pandas as pd
import streamlit as st

from backend.design_io import validate_design

# ---------------------------------------------------------------------------
# Tool schema (Anthropic format)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [{
    "name": "set_flows",
    "description": (
        "Replace the entire flows list of the current design. "
        "Provide the COMPLETE updated list, not a diff. "
        "Per source node, the sum of share values must equal 100. "
        "Source_layer is 0 for the leftmost 'Source' node; downstream "
        "medicine layers are 1, 2, etc. Use exactly the medicine names "
        "listed in the system prompt — do not invent new ones. "
        "NEVER emit a flow where `source` equals `target` (case-insensitive) "
        "— 'patients staying on the same drug' / 'responder %' flows are "
        "rejected; only model transitions to a DIFFERENT medicine."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "flows": {
                "type": "array",
                "description": "Complete updated list of flows.",
                "items": {
                    "type": "object",
                    "properties": {
                        "source":       {"type": "string"},
                        "source_layer": {"type": "integer", "minimum": 0},
                        "target":       {"type": "string"},
                        "target_layer": {"type": "integer", "minimum": 1},
                        "share":        {"type": "number", "minimum": 0, "maximum": 100},
                    },
                    "required": ["source", "source_layer", "target", "target_layer", "share"],
                },
            }
        },
        "required": ["flows"],
    },
}]

MAX_TOOL_ITERATIONS = 4


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _cfg() -> dict | None:
    try:
        return dict(st.secrets["azure_llm"])
    except (KeyError, FileNotFoundError, AttributeError):
        return None


def is_configured() -> bool:
    cfg = _cfg()
    if not cfg:
        return False
    required = ("endpoint", "deployment_name", "api_key")
    return all(str(cfg.get(k, "")).strip() and str(cfg.get(k, "")).strip() != "YOUR_API_KEY"
               for k in required)


@st.cache_resource(show_spinner=False)
def get_client():
    """Construct (and cache) an AnthropicFoundry client from secrets."""
    cfg = _cfg() or {}
    from anthropic import AnthropicFoundry  # type: ignore
    return AnthropicFoundry(
        api_key=str(cfg["api_key"]).strip(),
        base_url=str(cfg["endpoint"]).strip(),
    )


def _deployment_name() -> str:
    cfg = _cfg() or {}
    return str(cfg.get("deployment_name", "")).strip()


def _max_tokens() -> int:
    cfg = _cfg() or {}
    try:
        return int(cfg.get("max_tokens", 2048) or 2048)
    except (TypeError, ValueError):
        return 2048


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _flows_table(flows: list[dict]) -> str:
    if not flows:
        return "(geen flows — de tabel is leeg)"
    rows = ["| source | source_layer | target | target_layer | share % |",
            "|---|---|---|---|---|"]
    for f in flows:
        rows.append(
            f"| {f.get('source','')} | {int(f.get('source_layer',0))} | "
            f"{f.get('target','')} | {int(f.get('target_layer',1))} | "
            f"{float(f.get('share',0)):.1f} |"
        )
    return "\n".join(rows)


def _available_medicines(catalogue_df: pd.DataFrame, design: dict) -> list[str]:
    if catalogue_df is None or catalogue_df.empty or "medicine_name" not in catalogue_df.columns:
        return []
    cat = catalogue_df.copy()
    diag_id = str(design.get("diag_id_euk", "") or "").strip()
    if diag_id and "diag_id_euk" in cat.columns:
        sub = cat[cat["diag_id_euk"].astype(str).str.strip() == diag_id]
        if not sub.empty:
            cat = sub
    meds = (
        cat["medicine_name"].dropna().astype(str).str.strip()
        .replace("", pd.NA).dropna().unique().tolist()
    )
    return sorted(meds)


def build_system_prompt(
    design: dict,
    selected_diag: str,
    catalogue_df: pd.DataFrame,
) -> str:
    meds = _available_medicines(catalogue_df, design)
    meds_block = ", ".join(meds) if meds else "(geen medicijnen in catalogus voor deze diagnose)"
    flows_block = _flows_table(design.get("flows", []))

    return f"""Je bent een ontwerphulp voor een Streamlit-app waarmee een apotheker een 'ideaal' patiëntenpad ontwerpt voor een diagnose. Je werkt met 100 hypothetische patiënten die door medicijn-knopen stromen.

DIAGNOSE: {selected_diag}

HUIDIGE FLOWS (dit is precies wat de Sankey hierboven toont):
{flows_block}

CONVENTIES:
- Layer 0 = de 'Source'-knoop (de pool van 100 patiënten links).
- Layer 1, 2, … = opeenvolgende medicijn-stappen rechts daarvan.
- Per `(source, source_layer)` moet de som van `share` exact 100 zijn.
- Een 'flow' is een edge: {{source, source_layer, target, target_layer, share}}.

VERBODEN: GEEN 'RESPONDERS'-FLOWS
- Maak NOOIT een flow waarbij `source` en `target` hetzelfde medicijn zijn
  (case-insensitief). 'X% blijft op medicijn A' / 'X% responders op A' is voor
  dit ontwerp irrelevant — de apotheker is alleen geïnteresseerd in écht
  overstappen naar een ander medicijn. Patiënten die 'blijven' op een
  medicijn worden geen aparte flow.
- Als de gebruiker zegt "60% reageert op medicijn A", interpreteer dat als
  "60% komt op medicijn A te beginnen" (Source → A), NIET als een tweede
  laag waarin A naar A blijft.

BESCHIKBARE MEDICIJN-NAMEN (gebruik exact deze, niet zelf verzinnen):
{meds_block}

TAAK:
- Beantwoord de vraag in het Nederlands, kort en concreet.
- Als de gebruiker iets wil aanpassen aan de flows, gebruik dan ALTIJD de tool `set_flows` met de COMPLETE nieuwe lijst — niet alleen de wijziging.
- Behoud bestaande flows die niet gewijzigd hoeven te worden.
- Zorg dat per bron de aandelen optellen tot 100 (rond af op 1 decimaal indien nodig).
- Als de wens onduidelijk is, stel één korte vervolgvraag in plaats van te raden.
- Bij conversatie zonder ontwerp-wijziging (vraag/uitleg) hoef je geen tool te gebruiken.
"""


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _apply_set_flows(design: dict, tool_input: dict) -> tuple[dict, list[str]]:
    """
    Build a candidate design with `tool_input["flows"]` applied. Returns
    (candidate_design, errors). Errors come from `validate_design`, but only
    the flows-related ones — Prod_ID-mix completeness is not the chatbot's job.
    """
    raw = tool_input.get("flows", []) or []
    new_flows: list[dict] = []
    for r in raw:
        try:
            new_flows.append({
                "source":       str(r.get("source", "")).strip(),
                "source_layer": int(r.get("source_layer", 0)),
                "target":       str(r.get("target", "")).strip(),
                "target_layer": int(r.get("target_layer", 1)),
                "share":        float(r.get("share", 0) or 0),
            })
        except (TypeError, ValueError) as exc:
            return design, [f"Ongeldige rij in flows: {r!r} ({exc})"]

    new_flows = [f for f in new_flows if f["source"] and f["target"]]

    # Reject self-flows (source == target, case-insensitive). 'Responder' /
    # 'patient stays on same drug' edges are explicitly disallowed for this
    # design — only transitions to a DIFFERENT medicine are meaningful.
    self_loops = [
        f for f in new_flows
        if f["source"].strip().lower() == f["target"].strip().lower()
    ]
    if self_loops:
        offenders = ", ".join(
            f"{f['source']}→{f['target']} (laag {f['source_layer']}→{f['target_layer']})"
            for f in self_loops
        )
        return design, [
            "Self-loop flows zijn verboden (source = target). Verwijder deze "
            f"rijen en herverdeel de aandelen over écht andere medicijnen: {offenders}"
        ]

    candidate = copy.deepcopy(design)
    candidate["flows"] = new_flows

    all_errors = validate_design(candidate)
    flow_errors = [e for e in all_errors if "Prod_ID" not in e]
    return candidate, flow_errors


# ---------------------------------------------------------------------------
# Chat turn
# ---------------------------------------------------------------------------

def _content_to_text(content_blocks) -> str:
    parts: list[str] = []
    for block in content_blocks or []:
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "text":
            text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else "")
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def _block_to_dict(block) -> dict:
    """Normalise an SDK block (object or dict) to a plain dict for re-sending."""
    if isinstance(block, dict):
        return block
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type":  "tool_use",
            "id":    getattr(block, "id", ""),
            "name":  getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
        }
    if btype == "thinking":
        return {"type": "thinking", "thinking": getattr(block, "thinking", "")}
    return {"type": btype or "text", "text": str(block)}


def chat_turn(
    messages: list[dict],
    design: dict,
    selected_diag: str,
    catalogue_df: pd.DataFrame,
) -> dict[str, Any]:
    """
    Run one user turn through the model, looping over tool_use → tool_result
    until the model returns a final text response or we hit the iteration cap.

    `messages` is the full chat history as a list of `{role, content}` dicts
    (content can be a string for plain text or a list of content blocks).
    The caller is responsible for appending the user's new message before
    invoking this function.

    Returns:
        {
            "assistant_text": str,           # the final assistant text
            "new_design":      dict,         # design after any tool_use applied
            "design_changed":  bool,
            "messages":        list[dict],   # updated history including assistant turns
            "error":           str | None,
        }
    """
    if not is_configured():
        return {
            "assistant_text": "",
            "new_design":     design,
            "design_changed": False,
            "messages":       messages,
            "error":          "LLM is niet geconfigureerd (zie .streamlit/secrets.toml).",
        }

    try:
        client = get_client()
    except Exception as exc:
        return {
            "assistant_text": "",
            "new_design":     design,
            "design_changed": False,
            "messages":       messages,
            "error":          f"Kan Anthropic-client niet initialiseren: {exc}",
        }

    working_design = copy.deepcopy(design)
    convo: list[dict] = list(messages)
    final_text = ""
    error: str | None = None

    for _ in range(MAX_TOOL_ITERATIONS):
        try:
            response = client.messages.create(
                model=_deployment_name(),
                system=build_system_prompt(working_design, selected_diag, catalogue_df),
                tools=TOOLS,
                max_tokens=_max_tokens(),
                messages=convo,
            )
        except Exception as exc:
            error = f"Modelfout: {exc}"
            break

        assistant_blocks = [_block_to_dict(b) for b in (response.content or [])]
        convo.append({"role": "assistant", "content": assistant_blocks})

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason != "tool_use":
            final_text = _content_to_text(response.content)
            break

        # Build tool_result blocks for every tool_use in this assistant turn.
        tool_results: list[dict] = []
        for block in assistant_blocks:
            if block.get("type") != "tool_use":
                continue
            tool_id   = block.get("id", "")
            tool_name = block.get("name", "")
            tool_in   = block.get("input", {}) or {}

            if tool_name == "set_flows":
                candidate, errs = _apply_set_flows(working_design, tool_in)
                if errs:
                    tool_results.append({
                        "type":         "tool_result",
                        "tool_use_id":  tool_id,
                        "is_error":     True,
                        "content":      "Validatiefouten:\n- " + "\n- ".join(errs)
                                        + "\nPas de flows aan en roep set_flows opnieuw aan.",
                    })
                else:
                    working_design = candidate
                    by_src: dict[tuple, float] = {}
                    for f in working_design["flows"]:
                        k = (f["source"], int(f["source_layer"]))
                        by_src[k] = by_src.get(k, 0.0) + float(f["share"])
                    summary_lines = [
                        f"- {src} (laag {lay}): {tot:.1f}% over "
                        f"{sum(1 for f in working_design['flows'] if f['source']==src and int(f['source_layer'])==lay)} flows"
                        for (src, lay), tot in by_src.items()
                    ]
                    tool_results.append({
                        "type":         "tool_result",
                        "tool_use_id":  tool_id,
                        "content":      "Flows bijgewerkt. Nieuwe verdeling per bron:\n"
                                        + "\n".join(summary_lines),
                    })
            else:
                tool_results.append({
                    "type":         "tool_result",
                    "tool_use_id":  tool_id,
                    "is_error":     True,
                    "content":      f"Onbekende tool: {tool_name}",
                })

        if not tool_results:
            final_text = _content_to_text(response.content)
            break

        convo.append({"role": "user", "content": tool_results})
    else:
        # Loop exhausted without break.
        if not final_text:
            final_text = ("Ik heb het maximum aantal interne stappen bereikt. "
                          "Probeer je vraag wat specifieker te formuleren.")

    design_changed = (working_design.get("flows") != design.get("flows"))

    return {
        "assistant_text": final_text or "(leeg antwoord)",
        "new_design":     working_design,
        "design_changed": design_changed,
        "messages":       convo,
        "error":          error,
    }
