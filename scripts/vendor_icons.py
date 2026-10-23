"""Build the vendored icon packs (aws, azure, gcp) in arcviz/static/vendor/icons from pinned CC0 icon sets.

Usage: python scripts/vendor_icons.py

Spec: docs/architecture/specs/cloud-icons.md, ADR-0036. Downloads the pinned `@iconify-json/logos` and
`@iconify-json/simple-icons` tarballs from the npm registry, picks the icons listed in PACKS, writes one
Iconify JSON icon set per pack and README.md with sources, licences, the trademark note and the sizes.
Monochrome Simple Icons are recoloured with the vendor colour (CC0 allows modification). Commit the
result; the app never downloads icons at runtime.
"""

from __future__ import annotations

import io
import json
import tarfile
import urllib.request
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parents[1] / "arcviz" / "static" / "vendor" / "icons"

SOURCES = {
    "logos": ("@iconify-json/logos", "1.2.15", "SVG Logos by Gil Barbara (svg-logos)", "CC0-1.0"),
    "simple-icons": ("@iconify-json/simple-icons", "1.2.99", "Simple Icons", "CC0-1.0"),
}

# pack -> (vendor colour for monochrome icons, {icon name in the pack: (source set, source icon)})
PACKS: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {
    "aws": (
        "#ff9900",
        {
            "ec2": ("logos", "aws-ec2"),
            "lambda": ("logos", "aws-lambda"),
            "s3": ("logos", "aws-s3"),
            "rds": ("logos", "aws-rds"),
            "aurora": ("logos", "aws-aurora"),
            "dynamodb": ("logos", "aws-dynamodb"),
            "sqs": ("logos", "aws-sqs"),
            "sns": ("logos", "aws-sns"),
            "api-gateway": ("logos", "aws-api-gateway"),
            "cloudfront": ("logos", "aws-cloudfront"),
            "ecs": ("logos", "aws-ecs"),
            "eks": ("logos", "aws-eks"),
            "fargate": ("logos", "aws-fargate"),
            "elb": ("logos", "aws-elb"),
            "route53": ("logos", "aws-route53"),
            "cognito": ("logos", "aws-cognito"),
            "eventbridge": ("logos", "aws-eventbridge"),
            "kinesis": ("logos", "aws-kinesis"),
            "elasticache": ("logos", "aws-elasticache"),
            "step-functions": ("logos", "aws-step-functions"),
            "secrets-manager": ("logos", "aws-secrets-manager"),
            "cloudwatch": ("logos", "aws-cloudwatch"),
        },
    ),
    "azure": (
        "#0078d4",
        {
            "azure": ("logos", "azure-icon"),  # used for services without an openly licensed icon
            "functions": ("simple-icons", "azurefunctions"),
            "devops": ("simple-icons", "azuredevops"),
            "pipelines": ("simple-icons", "azurepipelines"),
            "data-explorer": ("simple-icons", "azuredataexplorer"),
            "sql": ("simple-icons", "microsoftsqlserver"),
            "kubernetes": ("logos", "kubernetes"),
            "redis": ("logos", "redis"),
            "postgresql": ("logos", "postgresql"),
        },
    ),
    "gcp": (
        "#4285f4",
        {
            "google-cloud": ("logos", "google-cloud"),
            "cloud-run": ("logos", "google-cloud-run"),
            "cloud-functions": ("logos", "google-cloud-functions"),
            "kubernetes": ("logos", "kubernetes"),
            "bigquery": ("simple-icons", "googlebigquery"),
            "bigtable": ("simple-icons", "googlebigtable"),
            "cloud-storage": ("simple-icons", "googlecloudstorage"),
            "spanner": ("simple-icons", "googlecloudspanner"),
            "pubsub": ("simple-icons", "googlepubsub"),
            "dataflow": ("simple-icons", "googledataflow"),
            "dataproc": ("simple-icons", "googledataproc"),
            "composer": ("simple-icons", "googlecloudcomposer"),
        },
    ),
}

TRADEMARK_NOTE = (
    "The icons are logos and service marks of Amazon Web Services, Microsoft, Google and the respective "
    "projects; they remain trademarks of their owners and are used only to name the service an element "
    "stands for. The icon *files* come from collections released under CC0-1.0 (public domain dedication)."
)


def fetch_set(package: str, version: str) -> dict:
    name = package.split("/")[-1]
    url = f"https://registry.npmjs.org/{package}/-/{name}-{version}.tgz"
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https host
        data = response.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = tar.extractfile("package/icons.json")
        if member is None:
            raise SystemExit(f"{package}@{version} has no package/icons.json")
        return json.load(member)


def main() -> None:
    sets = {key: fetch_set(package, version) for key, (package, version, _, _) in SOURCES.items()}
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    sizes: dict[str, int] = {}
    for pack, (colour, icons) in PACKS.items():
        out: dict = {"prefix": pack, "icons": {}, "width": 24, "height": 24}
        for name, (source, icon) in icons.items():
            data = sets[source]
            entry = dict(data["icons"][icon])
            entry.setdefault("width", data.get("width", 24))
            entry.setdefault("height", data.get("height", 24))
            if source == "simple-icons":
                entry["body"] = entry["body"].replace("currentColor", colour)
            out["icons"][name] = entry
            rows.append(f"| `{pack}:{name}` | {SOURCES[source][2]} `{icon}` | {SOURCES[source][3]} |")
        text = json.dumps(out, separators=(",", ":"), sort_keys=True) + "\n"
        (ICON_DIR / f"{pack}.json").write_text(text, encoding="utf-8")
        sizes[pack] = len(text.encode())
    total = sum(sizes.values())
    readme = [
        "# Vendored icon packs",
        "",
        "Built by `scripts/vendor_icons.py` (spec `docs/architecture/specs/cloud-icons.md`, ADR-0036). Do not edit",
        "by hand; change the script and re-run it.",
        "",
        "## Sources",
        "",
        "| Collection | npm package | Version | Licence |",
        "|---|---|---|---|",
        *(f"| {title} | `{package}` | {version} | {lic} |" for package, version, title, lic in SOURCES.values()),
        "",
        TRADEMARK_NOTE,
        "",
        "The official AWS, Azure and Google Cloud architecture icon sets are not included: their terms allow",
        "using them to create diagrams and documentation, not redistributing them inside a software package.",
        "",
        "## Sizes",
        "",
        "| Pack | Icons | Bytes |",
        "|---|---|---|",
        *(f"| `{pack}` | {len(PACKS[pack][1])} | {size} |" for pack, size in sizes.items()),
        f"| total | {sum(len(p[1]) for p in PACKS.values())} | {total} |",
        "",
        "## Icons",
        "",
        "| Icon | Source | Licence |",
        "|---|---|---|",
        *rows,
        "",
    ]
    (ICON_DIR / "README.md").write_text("\n".join(readme), encoding="utf-8")
    print(f"wrote {len(PACKS)} packs ({total / 1000:.1f} kB) to {ICON_DIR}")


if __name__ == "__main__":
    main()
