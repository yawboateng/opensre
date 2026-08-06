"""Google Cloud Platform environment variable names.

Distinct from the ``VERTEX_AI_*`` names in :mod:`config.constants.llm`: those
select the LLM that powers the agent, these select the GCP estate the agent
*investigates*. They are frequently the same project, but conflating them
breaks any deployment that runs Vertex in one project and infrastructure in
another.
"""

from __future__ import annotations

from typing import Final

#: Primary project every tool call defaults to.
GCP_PROJECT_ID_ENV: Final[str] = "GCP_PROJECT_ID"

#: Google's own standard project variable, honoured as a fallback so a pod that
#: already sets it for the client libraries needs no extra configuration.
GOOGLE_CLOUD_PROJECT_ENV: Final[str] = "GOOGLE_CLOUD_PROJECT"

#: Comma-separated additional project IDs reachable with the same credential.
#: Accepts literal ids, :data:`GCP_DISCOVER_PROJECTS_TOKEN`, or a mix of both.
#: There is deliberately no ``*`` here: ``*`` is what a *caller* passes to mean
#: "every configured project", and honouring it as a config value too would
#: make one character mean both "all of them" and "the set of all of them",
#: which cannot be resolved without knowing which side wrote it.
GCP_ADDITIONAL_PROJECTS_ENV: Final[str] = "GCP_ADDITIONAL_PROJECTS"

#: Value for :data:`GCP_ADDITIONAL_PROJECTS_ENV` meaning "ask Cloud Resource
#: Manager which projects this credential can see, and allow all of them".
#: A word rather than a symbol because it names an *action* with a latency and
#: a permission cost (``resourcemanager.projects.list``), not a pattern match.
GCP_DISCOVER_PROJECTS_TOKEN: Final[str] = "discover"

#: Service-account JSON — either the literal document or a path to it. Ends in
#: ``_KEY`` so it is keyring-eligible and must be read through
#: ``resolve_env_credential``, never bare ``os.getenv``.
GCP_SERVICE_ACCOUNT_KEY_ENV: Final[str] = "GCP_SERVICE_ACCOUNT_KEY"

#: Service account to impersonate on top of the ambient credential. Lets one
#: Workload Identity binding reach several estates without minting JSON keys.
GCP_IMPERSONATE_SERVICE_ACCOUNT_ENV: Final[str] = "GCP_IMPERSONATE_SERVICE_ACCOUNT"

#: Default page size for log/metric queries.
GCP_MAX_RESULTS_ENV: Final[str] = "GCP_MAX_RESULTS"

#: JSON array registering several named GCP estates at once. See
#: ``docs/multi-instance-integrations.mdx``.
GCP_INSTANCES_ENV: Final[str] = "GCP_INSTANCES"

#: Opt-in: register discovered GKE clusters as Kubernetes instances at process
#: start. Off unless set, because it widens what the agent can read — every
#: cluster the credential can enumerate in the configured projects becomes a
#: source for the ``kubernetes_*`` tools, and pod logs and configmaps routinely
#: carry credentials and personal data.
#: Accepts ``true`` for every configured project, the same comma-separated
#: project grammar the GCP tools take, or ``project/cluster`` entries to narrow
#: it to named clusters — the granularity that matters when a project holds one
#: cluster worth investigating and several that are not the agent's business.
GCP_AUTO_REGISTER_GKE_ENV: Final[str] = "GCP_AUTO_REGISTER_GKE"

__all__ = [
    "GCP_ADDITIONAL_PROJECTS_ENV",
    "GCP_AUTO_REGISTER_GKE_ENV",
    "GCP_DISCOVER_PROJECTS_TOKEN",
    "GCP_IMPERSONATE_SERVICE_ACCOUNT_ENV",
    "GCP_INSTANCES_ENV",
    "GCP_MAX_RESULTS_ENV",
    "GCP_PROJECT_ID_ENV",
    "GCP_SERVICE_ACCOUNT_KEY_ENV",
    "GOOGLE_CLOUD_PROJECT_ENV",
]
