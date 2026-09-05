"""The similar-idea library (Concept Paper §IV.e).

The scheme requires each Filtering Committee to group similar ideas and maintain
a library linking them to submitters and offices — explicitly *not* as a ground
for rejection, but so that similar submissions can be consolidated, mentored
together, and scaled as one.

Similarity here is computed from the submission text with TF-IDF and cosine
distance, deliberately not with an embedding model call. Two reasons: it is
free and repeatable, and the grouping is a clerical aid for a committee rather
than a judgement, so an explainable method beats an opaque one. The top shared
terms are recorded for each cluster so a Group Officer can see *why* two
submissions were grouped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import delete, select

from .config import load_config
from .db import init_db, session_scope
from .models import CanonicalRecordRow, Cluster, ClusterMember, Submission
from .schema import SubmissionContent

# Departmental vocabulary that appears in nearly every submission and would
# otherwise dominate the similarity computation.
DOMAIN_STOPWORDS = [
    "audit", "auditing", "accounts", "accounting", "office", "offices", "cag",
    "department", "departmental", "iaad", "sai", "india", "initiative",
    "innovation", "solution", "process", "system", "systems", "data",
    "will", "would", "can", "may", "shall", "also", "however", "therefore",
    "submission", "form", "wing", "team", "officer", "officers", "staff",
    "improve", "improvement", "efficiency", "effective", "effectiveness",
]


@dataclass
class ClusterInfo:
    label: str
    members: list[str] = field(default_factory=list)
    top_terms: list[str] = field(default_factory=list)
    mean_similarity: float = 0.0


def _corpus_text(content: SubmissionContent) -> str:
    """The fields that describe the idea. Deliberately excludes cost, office and
    technology detail, which make unrelated ideas look similar."""
    return " ".join(
        filter(None, [
            content.title,
            content.problem_statement,
            content.proposed_solution,
            content.beneficiaries,
        ])
    )


def build_idea_library() -> int:
    init_db()
    cfg = load_config()
    if not bool(cfg.get("pipeline.enable_clustering", True)):
        print("Clustering is disabled in config (pipeline.enable_clustering).")
        return 0

    threshold = float(cfg.get("pipeline.cluster_similarity", 0.82))

    with session_scope() as session:
        rows = list(
            session.execute(
                select(Submission.id, Submission.ref, Submission.title, CanonicalRecordRow.content)
                .join(CanonicalRecordRow, CanonicalRecordRow.submission_id == Submission.id)
                .order_by(Submission.ref)
            )
        )

    if len(rows) < 2:
        print(
            f"Only {len(rows)} mapped submission(s). Clustering needs at least two — "
            "it will become useful as the submission count grows."
        )
        return 0

    ids = [r[0] for r in rows]
    refs = [r[1] for r in rows]
    texts = [_corpus_text(SubmissionContent.model_validate(r[3])) for r in rows]

    usable = [i for i, t in enumerate(texts) if len(t.split()) >= 20]
    if len(usable) < 2:
        print("Not enough substantive text to compare submissions meaningfully.")
        return 0

    vectorizer = TfidfVectorizer(
        stop_words=list(TfidfVectorizer(stop_words="english").get_stop_words())
        + DOMAIN_STOPWORDS,
        ngram_range=(1, 2),
        min_df=1,
        sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform([texts[i] for i in usable])
    similarity = (matrix @ matrix.T).toarray()
    np.fill_diagonal(similarity, 1.0)
    distance = np.clip(1.0 - similarity, 0.0, None)

    clustering = AgglomerativeClustering(
        metric="precomputed",
        linkage="average",
        distance_threshold=1.0 - threshold,
        n_clusters=None,
    )
    labels = clustering.fit_predict(distance)

    groups: dict[int, list[int]] = {}
    for position, label in enumerate(labels):
        groups.setdefault(int(label), []).append(usable[position])

    feature_names = np.array(vectorizer.get_feature_names_out())
    run_id = "cluster-latest"

    infos: list[ClusterInfo] = []
    with session_scope() as session:
        # The library is rebuilt wholesale — it is a derived view, and a stale
        # partial library would mislead a committee.
        old_ids = [c.id for c in session.scalars(select(Cluster))]
        if old_ids:
            session.execute(delete(ClusterMember).where(ClusterMember.cluster_id.in_(old_ids)))
            session.execute(delete(Cluster).where(Cluster.id.in_(old_ids)))
            session.flush()

        for members in groups.values():
            if len(members) < 2:
                continue   # a cluster of one is not a group

            positions = [usable.index(m) for m in members]
            pair_scores = [
                similarity[a][b]
                for i, a in enumerate(positions)
                for b in positions[i + 1:]
            ]
            mean_sim = float(np.mean(pair_scores)) if pair_scores else 0.0

            centroid = np.asarray(matrix[positions].mean(axis=0)).ravel()
            top_idx = centroid.argsort()[::-1][:6]
            top_terms = [t for t in feature_names[top_idx] if centroid[feature_names.tolist().index(t)] > 0]

            label = ", ".join(top_terms[:4]) or "related submissions"
            cluster = Cluster(label=label, size=len(members), run_id=run_id)
            session.add(cluster)
            session.flush()
            for m in members:
                session.add(
                    ClusterMember(
                        cluster_id=cluster.id,
                        submission_id=ids[m],
                        similarity=round(mean_sim, 3),
                    )
                )
            infos.append(
                ClusterInfo(
                    label=label,
                    members=[refs[m] for m in members],
                    top_terms=top_terms,
                    mean_similarity=round(mean_sim, 3),
                )
            )

    if not infos:
        print(
            f"No groups of similar ideas found at a cosine threshold of {threshold}. "
            "Every submission is distinct on this measure."
        )
        return 0

    print(f"Idea library rebuilt — {len(infos)} group(s) of similar ideas:\n")
    for info in sorted(infos, key=lambda c: -len(c.members)):
        print(f"  [{info.mean_similarity:.2f}] {info.label}")
        print(f"      {', '.join(info.members)}")
    print(
        "\nSimilarity is not a ground for rejection. Each submission is scored on "
        "its own merits; this grouping exists so the committee can consolidate, "
        "mentor jointly, and consider a combined scaling team."
    )
    return 0
