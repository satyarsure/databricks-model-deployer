"""Where an artifact lives. Every source type has a handler that turns a path into the /Volumes path
the job reads; only the handlers for the types present run (so e.g. the S3 lookup is skipped when
nothing is on S3). Used for model artifacts and the evaluation dataset alike.

To add a source type: add it to ARTIFACT_TYPES in app/server/server.ts (+ the form options in
DeployModel.tsx), then register a handler in SOURCE_HANDLERS.

S3: every S3 location is registered in UC as an EXTERNAL VOLUME, so an S3 artifact is read through
that volume — exactly like a UC Volume artifact (UC-governed, no boto3 / AWS credentials). The
covering volume is looked up in system.information_schema.volumes (longest storage_location prefix
wins) and the s3:// path is rewritten to /Volumes/<cat>/<schema>/<vol>/<rest>. information_schema
only lists volumes the job's run-as identity holds a privilege on, so it needs READ VOLUME.
"""
import re

S3_SCHEME = re.compile(r"^s3[an]?://", re.IGNORECASE)


def s3_to_volume_path(spark, s3_path):
    p = S3_SCHEME.sub("s3://", str(s3_path).strip()).rstrip("/")
    rows = spark.sql(
        """WITH v AS (
             SELECT volume_catalog, volume_schema, volume_name,
                    regexp_replace(regexp_replace(storage_location, '^[sS]3[aAnN]?://', 's3://'),
                                   '/+$', '') AS loc
             FROM system.information_schema.volumes
             WHERE volume_type = 'EXTERNAL' AND storage_location IS NOT NULL)
           SELECT * FROM v
           WHERE :p = loc OR startswith(:p, concat(loc, '/'))
           ORDER BY length(loc) DESC LIMIT 1""",
        args={"p": p},
    ).collect()
    if not rows:
        raise ValueError(
            f"No Unity Catalog external volume covers {s3_path} (or the job's identity has no access "
            f"to it). Register the S3 location as an external volume and grant READ VOLUME on it.")
    r = rows[0]
    rest = p[len(r["loc"]):].lstrip("/")
    return "/".join(["/Volumes", r["volume_catalog"], r["volume_schema"], r["volume_name"]]
                    + ([rest] if rest else []))


# source type -> handler(spark, path) returning the /Volumes path to read.
SOURCE_HANDLERS = {
    "uc_volume": lambda spark, path: path,   # already a /Volumes path, read in place
    "s3": s3_to_volume_path,                 # via the UC external volume covering the S3 location
}


def eval_dataset_type(spec):
    """The eval dataset's source type: the form's choice, but an s3:// path is always treated as S3
    (so it can't be read unresolved); an untyped path is classified by its shape. "" = no dataset."""
    ev = (spec.get("eval_dataset") or "").strip()
    if not ev:
        return ""
    t = (spec.get("eval_dataset_type") or "").lower() or ("s3" if S3_SCHEME.match(ev) else "uc_volume")
    if S3_SCHEME.match(ev) and t != "s3":
        print(f"[sources] eval dataset {ev} is an S3 URI but typed {t!r}; treating it as s3")
        t = "s3"
    return t
