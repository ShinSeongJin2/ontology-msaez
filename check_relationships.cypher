// 관계 확인 쿼리
// Neo4j Browser에서 실행하여 관계가 제대로 생성되었는지 확인

// 1. IMPACTS_AGGREGATE 관계 확인
MATCH (us:UserStory {id:'US_001'})-[r:IMPACTS_AGGREGATE]->(agg:Aggregate)
RETURN us.id AS story_id, agg.id AS agg_id, r.confidence AS confidence;

// 2. IMPACTS_FIELD 관계 확인
MATCH (ac:AcceptanceCriterion {id:'AC_001'})-[r:IMPACTS_FIELD]->(f:Field)
RETURN ac.id AS ac_id, f.id AS field_id, r.confidence AS confidence;

// 3. COVERS_COMMAND 관계 확인
MATCH (ac:AcceptanceCriterion {id:'AC_001'})-[r:COVERS_COMMAND]->(cmd:Command)
RETURN ac.id AS ac_id, cmd.id AS cmd_id, r.confidence AS confidence;

// 4. COVERS_EVENT 관계 확인
MATCH (ac:AcceptanceCriterion {id:'AC_001'})-[r:COVERS_EVENT]->(evt:Event)
RETURN ac.id AS ac_id, evt.id AS evt_id, r.confidence AS confidence;

// 5. ES chain 확인
MATCH (cmd:Command {id:'CMD_PLACE_ORDER'})-[:EMITS_EVENT]->(evt:Event)
<-[:LISTENS_EVENT]-(pol:Policy)
RETURN cmd.id AS cmd_id, evt.id AS evt_id, pol.id AS pol_id;

// 6. AFFECTS_AGGREGATE 관계 확인
MATCH (evt:Event {id:'EVT_ORDER_PLACED'})-[:AFFECTS_AGGREGATE]->(agg:Aggregate)
RETURN evt.id AS evt_id, agg.id AS agg_id;

