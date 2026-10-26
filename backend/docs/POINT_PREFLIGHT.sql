-- v7 read-only ledger review. Do not modify financial rows to hide inconsistencies.
BEGIN TRANSACTION READ ONLY;
SELECT member_id, match_id, count(*) AS stake_count
FROM lolpick.lolpick_point_transactions WHERE reason=5
GROUP BY member_id,match_id HAVING count(*)>1;
SELECT p.id,p.member_id,p.related_entry_id,p.reason,p.amount
FROM lolpick.lolpick_point_transactions p
LEFT JOIN lolpick.lolpick_point_transactions s ON s.id=p.related_entry_id
WHERE p.reason IN (6,7) AND
(s.id IS NULL OR s.reason<>5 OR s.member_id<>p.member_id
 OR s.match_id<>p.match_id OR s.season_id<>p.season_id
 OR p.amount<=0 OR (p.reason=7 AND p.amount<>-s.amount));
SELECT related_entry_id,count(*) AS final_count
FROM lolpick.lolpick_point_transactions WHERE reason IN (6,7)
GROUP BY related_entry_id HAVING count(*)>1;
SELECT m.id,m.current_points,COALESCE(sum(p.amount),0) AS ledger_points
FROM lolpick.lolpick_members m
LEFT JOIN lolpick.lolpick_point_transactions p
 ON p.member_id=m.id AND p.season_id=m.current_season_id
WHERE m.current_season_id IS NOT NULL
GROUP BY m.id,m.current_points
HAVING m.current_points<>COALESCE(sum(p.amount),0);
COMMIT;
