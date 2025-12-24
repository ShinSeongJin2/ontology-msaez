// Zero-base SDD v1.1 — migrate Element label
// Purpose: add :Element label to domain/behavior nodes so Neo4j Browser can colorize by type.
// Safe to re-run.

// Domain / Behavior elements only (NOT Requirements, NOT Run/Change)
MATCH (n:BoundedContext) SET n:Element;
MATCH (n:Aggregate)      SET n:Element;
MATCH (n:Entity)         SET n:Element;
MATCH (n:ValueObject)    SET n:Element;
MATCH (n:Field)          SET n:Element;
MATCH (n:Command)        SET n:Element;
MATCH (n:Event)          SET n:Element;
MATCH (n:Policy)         SET n:Element;

// Optional: verify counts
// MATCH (e:Element) RETURN labels(e)[0] AS primaryLabel, count(*) AS cnt ORDER BY cnt DESC;
