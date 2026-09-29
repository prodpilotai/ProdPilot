"""The revision's figures, drawn from the JSON files in docs/revision.

Run with any Python that has matplotlib; the package itself does not need it:

    python tests/figures.py docs/revision OUT_DIR

Each figure is written as a vector PDF at the one-column width of the Springer
Nature template, 372 pt, so its type prints at the size it was set in. The
palette is the one the earlier figures use: blue, orange and aqua for STATIC,
DYNAMIC-PARAMETRIC and DYNAMIC-DELEGATED.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WIDTH = 372 / 72
INK, SECONDARY, GRID, AXIS = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
SLOT = ("#2a78d6", "#eb6834", "#1baf7a")
GRAY = "#898781"
TYPES = ("STATIC", "DYNAMIC-PARAMETRIC", "DYNAMIC-DELEGATED")
SHORT = {"STATIC": "Static", "DYNAMIC-PARAMETRIC": "Parametric", "DYNAMIC-DELEGATED": "Delegated"}
NAMES = {"D": "Model D", "E": "Model E", "build_only": "Build and stack",
         "audit_score": "Audit score", "rule_gate": "Rule-only gate"}
STYLE = {"D": (SLOT[0], "-"), "E": (SLOT[1], "--"), "build_only": (SLOT[2], "-."),
         "audit_score": (GRAY, ":"), "rule_gate": (INK, "")}


def setup():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 8, "axes.labelsize": 8,
        "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
        "legend.frameon": False, "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
        "axes.labelcolor": SECONDARY, "xtick.color": SECONDARY, "ytick.color": SECONDARY,
        "pdf.fonttype": 42, "axes.spines.top": False, "axes.spines.right": False,
    })
    return plt


def grid(ax, axis: str = "x") -> None:
    ax.grid(axis=axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def fix_types(plt, rq2: dict, out: Path) -> None:
    rows = [("All repositories", rq2["by_type"])]
    rows += [(("Express" if s == "node_express" else "React") + " repositories", v["by_type"])
             for s, v in rq2["by_stack"].items()]
    fig, ax = plt.subplots(figsize=(WIDTH, 1.55))
    for y, (label, shares) in enumerate(reversed(rows)):
        left = 0.0
        for j, t in enumerate(TYPES):
            w = shares[t]["share"] * 100
            ax.barh(y, w, left=left, height=0.58, color=SLOT[j], zorder=3,
                    label=SHORT[t] if y == 0 else None)
            if w >= 5:
                ax.text(left + w / 2, y, f"{w:.1f}%", ha="center", va="center", color="white",
                        fontsize=7)
            left += w
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in reversed(rows)])
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of failing rule instances, %")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=3)
    grid(ax)
    fig.savefig(out / "rq2_fix_types.pdf", bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def curves(plt, model: dict, out: Path) -> None:
    fig, (roc, pr) = plt.subplots(1, 2, figsize=(WIDTH, 2.45))
    held = model["held_out_metrics"]
    for name in ("D", "E", "build_only", "audit_score"):
        c = model["curves"][name]
        colour, dash = STYLE[name]
        m = held[name]["overall"]
        roc.plot(c["fpr"], c["tpr"], color=colour, linestyle=dash, linewidth=1.3,
                 label=f"{NAMES[name]}, {m['roc_auc']:.3f}")
        pr.plot(c["recall"], c["precision"], color=colour, linestyle=dash, linewidth=1.3,
                drawstyle="steps-post", label=f"{NAMES[name]}, {m['pr_auc']:.3f}")
    g = held["rule_gate"]["overall"]
    fpr = g["fp"] / (g["fp"] + g["tn"])
    roc.scatter([fpr], [g["recall"]], color=INK, s=14, zorder=4, label="Rule-only gate")
    roc.plot([0, 1], [0, 1], color=AXIS, linewidth=0.7)
    base = held["D"]["overall"]["positive"] / held["D"]["overall"]["rows"]
    pr.axhline(base, color=AXIS, linewidth=0.7)
    for ax, (x, y) in ((roc, ("False positive rate", "True positive rate")),
                       (pr, ("Recall", "Precision"))):
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.02)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        grid(ax, "both")
        ax.legend(loc="lower right" if ax is roc else "upper right", fontsize=6.3)
    fig.tight_layout(w_pad=1.5)
    fig.savefig(out / "model_curves.pdf", bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def forest(plt, model: dict, out: Path) -> None:
    order = ("D", "E", "build_only", "audit_score")
    parts = (("overall", "all"), ("react_vite", "React"), ("node_express", "Express"))
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 2.9), sharey=True)
    labels = []
    for i, (name, (part, word)) in enumerate([(n, p) for n in order for p in parts]):
        labels.append(f"{NAMES[name]}, {word}")
    for ax, key, title in ((axes[0], "roc_auc", "ROC AUC"), (axes[1], "pr_auc", "PR AUC")):
        y = len(labels) - 1
        for name in order:
            colour = STYLE[name][0]
            for part, _ in parts:
                m = model["held_out_metrics"][name][part].get(key)
                ci = model["bootstrap"]["ci"][name][part].get(key) or {}
                if m is not None and ci:
                    ax.plot([ci["low"], ci["high"]], [y, y], color=colour, linewidth=1.6,
                            solid_capstyle="butt")
                    ax.scatter([m], [y], color=colour, s=12, zorder=3)
                y -= 1
        ax.set_xlim(0, 1)
        ax.set_xlabel(f"Held-out {title}, 95% interval")
        grid(ax)
    axes[0].set_yticks(range(len(labels)))
    axes[0].set_yticklabels(list(reversed(labels)))
    fig.tight_layout(w_pad=1.0)
    fig.savefig(out / "model_forest.pdf", bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def confusion(plt, model: dict, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH * 0.72, 1.75))
    for ax, (stack, word) in zip(axes, (("react_vite", "React"), ("node_express", "Express"))):
        m = model["held_out_metrics"]["D"][stack]
        cells = [[m["tn"], m["fp"]], [m["fn"], m["tp"]]]
        top = max(max(r) for r in cells)
        ax.imshow(cells, cmap="Blues", vmin=0, vmax=top * 1.15)
        for i in range(2):
            for j in range(2):
                v = cells[i][j]
                ax.text(j, i, str(v), ha="center", va="center", fontsize=9,
                        color="white" if v > top * 0.5 else INK)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["Refused", "Cleared"])
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["Failed", "Deployed"])
        ax.set_title(f"{word}, {m['rows']} rows", fontsize=8, color=INK)
        ax.tick_params(length=0)
        for side in ax.spines.values():
            side.set_visible(False)
    fig.tight_layout(w_pad=2.0)
    fig.savefig(out / "model_confusion.pdf", bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def arms(plt, agent: dict, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(WIDTH, 2.0))
    width = 0.36
    for k, (arm, word) in enumerate((("A", "Agent alone"), ("B", "With ProdPilot"))):
        by = agent["arms"].get(arm, {}).get("by_type", {})
        rates = [100 * (by.get(t, {}).get("rate") or 0) for t in TYPES]
        xs = [i + (k - 0.5) * width for i in range(len(TYPES))]
        ax.bar(xs, rates, width=width - 0.04, color=SLOT[k], label=word, zorder=3)
    ax.set_xticks(range(len(TYPES)))
    ax.set_xticklabels([SHORT[t] for t in TYPES])
    ax.set_ylim(0, 100)
    ax.set_ylabel("Failing rules fixed, %")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=2)
    grid(ax, "y")
    fig.savefig(out / "agent_arms.pdf", bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def main(src: str, dst: str) -> None:
    plt = setup()
    source, out = Path(src), Path(dst)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    if (source / "rq2.json").exists():
        fix_types(plt, json.loads((source / "rq2.json").read_text(encoding="utf-8")), out)
        made.append("rq2_fix_types.pdf")
    if (source / "model.json").exists():
        model = json.loads((source / "model.json").read_text(encoding="utf-8"))
        curves(plt, model, out)
        forest(plt, model, out)
        confusion(plt, model, out)
        made += ["model_curves.pdf", "model_forest.pdf", "model_confusion.pdf"]
    if (source / "agent.json").exists():
        arms(plt, json.loads((source / "agent.json").read_text(encoding="utf-8")), out)
        made.append("agent_arms.pdf")
    print("wrote", ", ".join(made) or "nothing")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/revision",
         sys.argv[2] if len(sys.argv) > 2 else "docs/revision/figures")
