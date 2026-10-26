// Generated from LoLPICK OpenAPI v0.14.1. Do not hand-edit schema types.

export type BetOut = {
  id: number;
  seasonId: string;
  matchId: string;
  teamId: string;
  amount: number;
  status: "pending" | "won" | "lost" | "refunded";
  payout: number | null;
  cheerAction: "cheering" | "betrayal" | null;
  createdAt: string;
  currentPoints?: number | null;
  cheer?: CheerOut | null;
};

export type BetPage = {
  items: (BetOut)[];
  total: number;
  limit: number;
  offset: number;
};

export type BetRequest = {
  matchId: string;
  teamId: string;
  amount: number;
};

export type CheerOut = {
  id: number;
  seasonId: string;
  teamId: string;
  sealHeld: boolean;
  sealRevokedAt: string | null;
  finalizedAt: string | null;
  cheeringCount: number;
  betrayalCount: number;
  createdAt: string;
};

export type CheerRequest = {
  seasonId: string;
  teamId: string;
};

export type CompetitionOut = {
  id: string;
  name: string;
  slug?: string | null;
};

export type ErrorDetail = {
  code: string;
  message: string;
};

export type ErrorOut = {
  detail: ErrorDetail;
};

export type EventOut = {
  id: string;
  slug: string;
  name: string;
  year: number;
  label: string;
  active: boolean;
  status: "upcoming" | "active" | "finished";
  teams: (string)[];
  initialPoints: number;
  isDemo: boolean;
  startsAt?: number | null;
  endsAt?: number | null;
  stages: (EventStageOut)[];
};

export type EventStageOut = {
  name: string;
  startsAt: number | null;
  endsAt: number | null;
  matchCount: number;
  unscheduledCount: number;
};

export type HealthOut = {
  status: "ok";
  service: "lolpick-backend";
  version: string;
};

export type MatchContextOut = {
  matchId: string;
  seasonId: string;
  currentPoints: number;
  cheer: CheerOut | null;
  bet: BetOut | null;
  bettingOpen: boolean;
  eligible: boolean;
  reason: string | null;
  sessionExpiresAt: string;
  myRank?: number | null;
};

export type MatchOut = {
  id: string;
  event: string;
  season: string;
  round: string;
  teams: [string | null, string | null];
  teamDetails: [TeamOut | null, TeamOut | null];
  pools: [number, number];
  startsAt: number | null;
  status: "scheduled" | "live" | "finished" | "settled" | "cancelled";
  score: [number, number] | null;
  winner: string | null;
  bettingOpen: boolean;
  seasonStatus?: "upcoming" | "active" | "finished" | null;
  result?: "team_a_win" | "team_b_win" | "draw" | "cancelled" | null;
  settlementStatus?: "pending" | "settled" | "refunded";
  settlementPool?: number | null;
  settlementPaid?: number | null;
  removedRemainder?: number | null;
  isDemo: boolean;
  poolSource: "demo" | "bets";
  apiSource: string;
  providerStatus?: string | null;
  apiSyncedAt?: number | null;
  name?: string;
  matchType?: string | null;
  numberOfGames?: number | null;
  league?: CompetitionOut | null;
  tournament?: CompetitionOut | null;
  forfeit?: boolean;
  draw?: boolean;
  dataIssues?: (string)[];
  bettingClosedReason?: string | null;
};

export type MatchPage = {
  items: (MatchOut)[];
  total: number;
  limit: number;
  offset: number;
};

export type MatchResultRequest = {
  result: "team_a_win" | "team_b_win" | "draw" | "cancelled";
  score_a?: number | null;
  score_b?: number | null;
};

export type MemberOut = {
  memberId: string;
  currentPoints: number;
  currentSeasonId: string | null;
  availableSeasonIds?: (string)[];
  sessionExpiresAt?: string | null;
};

export type ReadyOut = {
  status: "ok";
  database: "connected";
  migration: string;
};

export type SeasonActivatedOut = {
  seasonId: string;
  status: "active";
  initializedMembers: number;
};

export type SeasonFinishedOut = {
  seasonId: string;
  status: "finished";
};

export type SeasonOut = {
  id: string;
  slug: string;
  name: string;
  year: number;
  label: string;
  active: boolean;
  status: "upcoming" | "active" | "finished";
  teams: (string)[];
  initialPoints: number;
  isDemo: boolean;
  startsAt?: number | null;
  endsAt?: number | null;
};

export type SettlementOut = {
  matchId: string;
  seasonId: string;
  result: "team_a_win" | "team_b_win" | "draw" | "cancelled" | null;
  winner: string | null;
  settlementStatus: "pending" | "settled" | "refunded";
  pool: number | null;
  paid: number | null;
  removedRemainder: number | null;
};

export type StakeRankOut = {
  rank: number;
  displayName?: string | null;
  amount: number;
  shareBasisPoints: number;
  sealHeld: boolean;
};

export type StakeRankingOut = {
  matchId: string;
  seasonId: string;
  teams: [string | null, string | null];
  pools: [number, number];
  items: [(StakeRankOut)[], (StakeRankOut)[]];
};

export type SyncSummaryOut = {
  seasonId: string;
  seriesId: number;
  received: number;
  created: number;
  updated: number;
  skipped_tbd: number;
  stored_tbd: number;
  stored_unscheduled: number;
  skipped_unscheduled: number;
  conflicts: number;
  stale: number;
  settled: number;
  refunded: number;
  settlement_blocked: number;
};

export type TeamOut = {
  id: string;
  name: string;
  region?: string;
  color?: string;
  logo?: string | null;
  acronym?: string | null;
  location?: string | null;
  apiSource: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function validBetOut(value: unknown): value is BetOut {
  return (isRecord(value) && (typeof value["id"] === 'number' && Number.isSafeInteger(value["id"])) && (typeof value["seasonId"] === 'string') && (typeof value["matchId"] === 'string') && (typeof value["teamId"] === 'string') && (typeof value["amount"] === 'number' && Number.isSafeInteger(value["amount"])) && (["pending", "won", "lost", "refunded"] as readonly unknown[]).includes(value["status"]) && ((typeof value["payout"] === 'number' && Number.isSafeInteger(value["payout"])) || value["payout"] === null) && ((["cheering", "betrayal"] as readonly unknown[]).includes(value["cheerAction"]) || value["cheerAction"] === null) && (typeof value["createdAt"] === 'string' && Number.isFinite(Date.parse(value["createdAt"]))) && (!("currentPoints" in value) || (((typeof value["currentPoints"] === 'number' && Number.isSafeInteger(value["currentPoints"])) || value["currentPoints"] === null))) && (!("cheer" in value) || ((validCheerOut(value["cheer"]) || value["cheer"] === null))));
}

function validBetPage(value: unknown): value is BetPage {
  return (isRecord(value) && (Array.isArray(value["items"]) && value["items"].every((item: unknown) => validBetOut(item))) && (typeof value["total"] === 'number' && Number.isSafeInteger(value["total"])) && (typeof value["limit"] === 'number' && Number.isSafeInteger(value["limit"])) && (typeof value["offset"] === 'number' && Number.isSafeInteger(value["offset"])));
}

function validBetRequest(value: unknown): value is BetRequest {
  return (isRecord(value) && (typeof value["matchId"] === 'string') && (typeof value["teamId"] === 'string') && (typeof value["amount"] === 'number' && Number.isSafeInteger(value["amount"]) && value["amount"] <= 9007199254740991.0 && value["amount"] > 0.0));
}

function validCheerOut(value: unknown): value is CheerOut {
  return (isRecord(value) && (typeof value["id"] === 'number' && Number.isSafeInteger(value["id"])) && (typeof value["seasonId"] === 'string') && (typeof value["teamId"] === 'string') && (typeof value["sealHeld"] === 'boolean') && ((typeof value["sealRevokedAt"] === 'string' && Number.isFinite(Date.parse(value["sealRevokedAt"]))) || value["sealRevokedAt"] === null) && ((typeof value["finalizedAt"] === 'string' && Number.isFinite(Date.parse(value["finalizedAt"]))) || value["finalizedAt"] === null) && (typeof value["cheeringCount"] === 'number' && Number.isSafeInteger(value["cheeringCount"])) && (typeof value["betrayalCount"] === 'number' && Number.isSafeInteger(value["betrayalCount"])) && (typeof value["createdAt"] === 'string' && Number.isFinite(Date.parse(value["createdAt"]))));
}

function validCheerRequest(value: unknown): value is CheerRequest {
  return (isRecord(value) && (typeof value["seasonId"] === 'string') && (typeof value["teamId"] === 'string'));
}

function validCompetitionOut(value: unknown): value is CompetitionOut {
  return (isRecord(value) && (typeof value["id"] === 'string') && (typeof value["name"] === 'string') && (!("slug" in value) || (((typeof value["slug"] === 'string') || value["slug"] === null))));
}

function validErrorDetail(value: unknown): value is ErrorDetail {
  return (isRecord(value) && (typeof value["code"] === 'string') && (typeof value["message"] === 'string'));
}

function validErrorOut(value: unknown): value is ErrorOut {
  return (isRecord(value) && validErrorDetail(value["detail"]));
}

function validEventOut(value: unknown): value is EventOut {
  return (isRecord(value) && (typeof value["id"] === 'string') && (typeof value["slug"] === 'string') && (typeof value["name"] === 'string') && (typeof value["year"] === 'number' && Number.isSafeInteger(value["year"])) && (typeof value["label"] === 'string') && (typeof value["active"] === 'boolean') && (["upcoming", "active", "finished"] as readonly unknown[]).includes(value["status"]) && (Array.isArray(value["teams"]) && value["teams"].every((item: unknown) => (typeof item === 'string'))) && (typeof value["initialPoints"] === 'number' && Number.isSafeInteger(value["initialPoints"])) && (typeof value["isDemo"] === 'boolean') && (!("startsAt" in value) || (((typeof value["startsAt"] === 'number' && Number.isSafeInteger(value["startsAt"])) || value["startsAt"] === null))) && (!("endsAt" in value) || (((typeof value["endsAt"] === 'number' && Number.isSafeInteger(value["endsAt"])) || value["endsAt"] === null))) && (Array.isArray(value["stages"]) && value["stages"].every((item: unknown) => validEventStageOut(item))));
}

function validEventStageOut(value: unknown): value is EventStageOut {
  return (isRecord(value) && (typeof value["name"] === 'string') && ((typeof value["startsAt"] === 'number' && Number.isSafeInteger(value["startsAt"])) || value["startsAt"] === null) && ((typeof value["endsAt"] === 'number' && Number.isSafeInteger(value["endsAt"])) || value["endsAt"] === null) && (typeof value["matchCount"] === 'number' && Number.isSafeInteger(value["matchCount"])) && (typeof value["unscheduledCount"] === 'number' && Number.isSafeInteger(value["unscheduledCount"])));
}

function validHealthOut(value: unknown): value is HealthOut {
  return (isRecord(value) && value["status"] === "ok" && value["service"] === "lolpick-backend" && (typeof value["version"] === 'string'));
}

function validMatchContextOut(value: unknown): value is MatchContextOut {
  return (isRecord(value) && (typeof value["matchId"] === 'string') && (typeof value["seasonId"] === 'string') && (typeof value["currentPoints"] === 'number' && Number.isSafeInteger(value["currentPoints"])) && (validCheerOut(value["cheer"]) || value["cheer"] === null) && (validBetOut(value["bet"]) || value["bet"] === null) && (typeof value["bettingOpen"] === 'boolean') && (typeof value["eligible"] === 'boolean') && ((typeof value["reason"] === 'string') || value["reason"] === null) && (typeof value["sessionExpiresAt"] === 'string' && Number.isFinite(Date.parse(value["sessionExpiresAt"]))) && (!("myRank" in value) || (((typeof value["myRank"] === 'number' && Number.isSafeInteger(value["myRank"])) || value["myRank"] === null))));
}

function validMatchOut(value: unknown): value is MatchOut {
  return (isRecord(value) && (typeof value["id"] === 'string') && (typeof value["event"] === 'string') && (typeof value["season"] === 'string') && (typeof value["round"] === 'string') && (Array.isArray(value["teams"]) && value["teams"].length === 2 && ((typeof value["teams"][0] === 'string') || value["teams"][0] === null) && ((typeof value["teams"][1] === 'string') || value["teams"][1] === null)) && (Array.isArray(value["teamDetails"]) && value["teamDetails"].length === 2 && (validTeamOut(value["teamDetails"][0]) || value["teamDetails"][0] === null) && (validTeamOut(value["teamDetails"][1]) || value["teamDetails"][1] === null)) && (Array.isArray(value["pools"]) && value["pools"].length === 2 && (typeof value["pools"][0] === 'number' && Number.isSafeInteger(value["pools"][0])) && (typeof value["pools"][1] === 'number' && Number.isSafeInteger(value["pools"][1]))) && ((typeof value["startsAt"] === 'number' && Number.isSafeInteger(value["startsAt"])) || value["startsAt"] === null) && (["scheduled", "live", "finished", "settled", "cancelled"] as readonly unknown[]).includes(value["status"]) && ((Array.isArray(value["score"]) && value["score"].length === 2 && (typeof value["score"][0] === 'number' && Number.isSafeInteger(value["score"][0])) && (typeof value["score"][1] === 'number' && Number.isSafeInteger(value["score"][1]))) || value["score"] === null) && ((typeof value["winner"] === 'string') || value["winner"] === null) && (typeof value["bettingOpen"] === 'boolean') && (!("seasonStatus" in value) || (((["upcoming", "active", "finished"] as readonly unknown[]).includes(value["seasonStatus"]) || value["seasonStatus"] === null))) && (!("result" in value) || (((["team_a_win", "team_b_win", "draw", "cancelled"] as readonly unknown[]).includes(value["result"]) || value["result"] === null))) && (!("settlementStatus" in value) || ((["pending", "settled", "refunded"] as readonly unknown[]).includes(value["settlementStatus"]))) && (!("settlementPool" in value) || (((typeof value["settlementPool"] === 'number' && Number.isSafeInteger(value["settlementPool"])) || value["settlementPool"] === null))) && (!("settlementPaid" in value) || (((typeof value["settlementPaid"] === 'number' && Number.isSafeInteger(value["settlementPaid"])) || value["settlementPaid"] === null))) && (!("removedRemainder" in value) || (((typeof value["removedRemainder"] === 'number' && Number.isSafeInteger(value["removedRemainder"])) || value["removedRemainder"] === null))) && (typeof value["isDemo"] === 'boolean') && (["demo", "bets"] as readonly unknown[]).includes(value["poolSource"]) && (typeof value["apiSource"] === 'string') && (!("providerStatus" in value) || (((typeof value["providerStatus"] === 'string') || value["providerStatus"] === null))) && (!("apiSyncedAt" in value) || (((typeof value["apiSyncedAt"] === 'number' && Number.isSafeInteger(value["apiSyncedAt"])) || value["apiSyncedAt"] === null))) && (!("name" in value) || ((typeof value["name"] === 'string'))) && (!("matchType" in value) || (((typeof value["matchType"] === 'string') || value["matchType"] === null))) && (!("numberOfGames" in value) || (((typeof value["numberOfGames"] === 'number' && Number.isSafeInteger(value["numberOfGames"])) || value["numberOfGames"] === null))) && (!("league" in value) || ((validCompetitionOut(value["league"]) || value["league"] === null))) && (!("tournament" in value) || ((validCompetitionOut(value["tournament"]) || value["tournament"] === null))) && (!("forfeit" in value) || ((typeof value["forfeit"] === 'boolean'))) && (!("draw" in value) || ((typeof value["draw"] === 'boolean'))) && (!("dataIssues" in value) || ((Array.isArray(value["dataIssues"]) && value["dataIssues"].every((item: unknown) => (typeof item === 'string'))))) && (!("bettingClosedReason" in value) || (((typeof value["bettingClosedReason"] === 'string') || value["bettingClosedReason"] === null))));
}

function validMatchPage(value: unknown): value is MatchPage {
  return (isRecord(value) && (Array.isArray(value["items"]) && value["items"].every((item: unknown) => validMatchOut(item))) && (typeof value["total"] === 'number' && Number.isSafeInteger(value["total"])) && (typeof value["limit"] === 'number' && Number.isSafeInteger(value["limit"])) && (typeof value["offset"] === 'number' && Number.isSafeInteger(value["offset"])));
}

function validMatchResultRequest(value: unknown): value is MatchResultRequest {
  return (isRecord(value) && (["team_a_win", "team_b_win", "draw", "cancelled"] as readonly unknown[]).includes(value["result"]) && (!("score_a" in value) || (((typeof value["score_a"] === 'number' && Number.isSafeInteger(value["score_a"]) && value["score_a"] >= 0.0 && value["score_a"] <= 32767.0) || value["score_a"] === null))) && (!("score_b" in value) || (((typeof value["score_b"] === 'number' && Number.isSafeInteger(value["score_b"]) && value["score_b"] >= 0.0 && value["score_b"] <= 32767.0) || value["score_b"] === null))));
}

function validMemberOut(value: unknown): value is MemberOut {
  return (isRecord(value) && (typeof value["memberId"] === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value["memberId"])) && (typeof value["currentPoints"] === 'number' && Number.isSafeInteger(value["currentPoints"])) && ((typeof value["currentSeasonId"] === 'string') || value["currentSeasonId"] === null) && (!("availableSeasonIds" in value) || ((Array.isArray(value["availableSeasonIds"]) && value["availableSeasonIds"].every((item: unknown) => (typeof item === 'string'))))) && (!("sessionExpiresAt" in value) || (((typeof value["sessionExpiresAt"] === 'string' && Number.isFinite(Date.parse(value["sessionExpiresAt"]))) || value["sessionExpiresAt"] === null))));
}

function validReadyOut(value: unknown): value is ReadyOut {
  return (isRecord(value) && value["status"] === "ok" && value["database"] === "connected" && (typeof value["migration"] === 'string'));
}

function validSeasonActivatedOut(value: unknown): value is SeasonActivatedOut {
  return (isRecord(value) && (typeof value["seasonId"] === 'string') && value["status"] === "active" && (typeof value["initializedMembers"] === 'number' && Number.isSafeInteger(value["initializedMembers"])));
}

function validSeasonFinishedOut(value: unknown): value is SeasonFinishedOut {
  return (isRecord(value) && (typeof value["seasonId"] === 'string') && value["status"] === "finished");
}

function validSeasonOut(value: unknown): value is SeasonOut {
  return (isRecord(value) && (typeof value["id"] === 'string') && (typeof value["slug"] === 'string') && (typeof value["name"] === 'string') && (typeof value["year"] === 'number' && Number.isSafeInteger(value["year"])) && (typeof value["label"] === 'string') && (typeof value["active"] === 'boolean') && (["upcoming", "active", "finished"] as readonly unknown[]).includes(value["status"]) && (Array.isArray(value["teams"]) && value["teams"].every((item: unknown) => (typeof item === 'string'))) && (typeof value["initialPoints"] === 'number' && Number.isSafeInteger(value["initialPoints"])) && (typeof value["isDemo"] === 'boolean') && (!("startsAt" in value) || (((typeof value["startsAt"] === 'number' && Number.isSafeInteger(value["startsAt"])) || value["startsAt"] === null))) && (!("endsAt" in value) || (((typeof value["endsAt"] === 'number' && Number.isSafeInteger(value["endsAt"])) || value["endsAt"] === null))));
}

function validSettlementOut(value: unknown): value is SettlementOut {
  return (isRecord(value) && (typeof value["matchId"] === 'string') && (typeof value["seasonId"] === 'string') && ((["team_a_win", "team_b_win", "draw", "cancelled"] as readonly unknown[]).includes(value["result"]) || value["result"] === null) && ((typeof value["winner"] === 'string') || value["winner"] === null) && (["pending", "settled", "refunded"] as readonly unknown[]).includes(value["settlementStatus"]) && ((typeof value["pool"] === 'number' && Number.isSafeInteger(value["pool"])) || value["pool"] === null) && ((typeof value["paid"] === 'number' && Number.isSafeInteger(value["paid"])) || value["paid"] === null) && ((typeof value["removedRemainder"] === 'number' && Number.isSafeInteger(value["removedRemainder"])) || value["removedRemainder"] === null));
}

function validStakeRankOut(value: unknown): value is StakeRankOut {
  return (isRecord(value) && (typeof value["rank"] === 'number' && Number.isSafeInteger(value["rank"])) && (!("displayName" in value) || (((typeof value["displayName"] === 'string') || value["displayName"] === null))) && (typeof value["amount"] === 'number' && Number.isSafeInteger(value["amount"])) && (typeof value["shareBasisPoints"] === 'number' && Number.isSafeInteger(value["shareBasisPoints"])) && (typeof value["sealHeld"] === 'boolean'));
}

function validStakeRankingOut(value: unknown): value is StakeRankingOut {
  return (isRecord(value) && (typeof value["matchId"] === 'string') && (typeof value["seasonId"] === 'string') && (Array.isArray(value["teams"]) && value["teams"].length === 2 && ((typeof value["teams"][0] === 'string') || value["teams"][0] === null) && ((typeof value["teams"][1] === 'string') || value["teams"][1] === null)) && (Array.isArray(value["pools"]) && value["pools"].length === 2 && (typeof value["pools"][0] === 'number' && Number.isSafeInteger(value["pools"][0])) && (typeof value["pools"][1] === 'number' && Number.isSafeInteger(value["pools"][1]))) && (Array.isArray(value["items"]) && value["items"].length === 2 && (Array.isArray(value["items"][0]) && value["items"][0].every((item: unknown) => validStakeRankOut(item))) && (Array.isArray(value["items"][1]) && value["items"][1].every((item: unknown) => validStakeRankOut(item)))));
}

function validSyncSummaryOut(value: unknown): value is SyncSummaryOut {
  return (isRecord(value) && (typeof value["seasonId"] === 'string') && (typeof value["seriesId"] === 'number' && Number.isSafeInteger(value["seriesId"])) && (typeof value["received"] === 'number' && Number.isSafeInteger(value["received"])) && (typeof value["created"] === 'number' && Number.isSafeInteger(value["created"])) && (typeof value["updated"] === 'number' && Number.isSafeInteger(value["updated"])) && (typeof value["skipped_tbd"] === 'number' && Number.isSafeInteger(value["skipped_tbd"])) && (typeof value["stored_tbd"] === 'number' && Number.isSafeInteger(value["stored_tbd"])) && (typeof value["stored_unscheduled"] === 'number' && Number.isSafeInteger(value["stored_unscheduled"])) && (typeof value["skipped_unscheduled"] === 'number' && Number.isSafeInteger(value["skipped_unscheduled"])) && (typeof value["conflicts"] === 'number' && Number.isSafeInteger(value["conflicts"])) && (typeof value["stale"] === 'number' && Number.isSafeInteger(value["stale"])) && (typeof value["settled"] === 'number' && Number.isSafeInteger(value["settled"])) && (typeof value["refunded"] === 'number' && Number.isSafeInteger(value["refunded"])) && (typeof value["settlement_blocked"] === 'number' && Number.isSafeInteger(value["settlement_blocked"])));
}

function validTeamOut(value: unknown): value is TeamOut {
  return (isRecord(value) && (typeof value["id"] === 'string') && (typeof value["name"] === 'string') && (!("region" in value) || ((typeof value["region"] === 'string'))) && (!("color" in value) || ((typeof value["color"] === 'string'))) && (!("logo" in value) || (((typeof value["logo"] === 'string') || value["logo"] === null))) && (!("acronym" in value) || (((typeof value["acronym"] === 'string') || value["acronym"] === null))) && (!("location" in value) || (((typeof value["location"] === 'string') || value["location"] === null))) && (typeof value["apiSource"] === 'string'));
}

const responseContracts = [
  { method: "GET", path: "/health", status: 200, validate: (value: unknown) => validHealthOut(value) },
  { method: "GET", path: "/ready", status: 200, validate: (value: unknown) => validReadyOut(value) },
  { method: "GET", path: "/teams", status: 200, validate: (value: unknown) => (Array.isArray(value) && value.every((item: unknown) => validTeamOut(item))) },
  { method: "GET", path: "/events", status: 200, validate: (value: unknown) => (Array.isArray(value) && value.every((item: unknown) => validSeasonOut(item))) },
  { method: "GET", path: "/seasons", status: 200, validate: (value: unknown) => (Array.isArray(value) && value.every((item: unknown) => validSeasonOut(item))) },
  { method: "GET", path: "/events/{event_id}", status: 200, validate: (value: unknown) => validEventOut(value) },
  { method: "GET", path: "/matches", status: 200, validate: (value: unknown) => validMatchPage(value) },
  { method: "GET", path: "/matches/{match_id}", status: 200, validate: (value: unknown) => validMatchOut(value) },
  { method: "GET", path: "/matches/{match_id}/stake-ranking", status: 200, validate: (value: unknown) => validStakeRankingOut(value) },
  { method: "POST", path: "/auth/session", status: 200, validate: (value: unknown) => validMemberOut(value) },
  { method: "GET", path: "/auth/me", status: 200, validate: (value: unknown) => validMemberOut(value) },
  { method: "POST", path: "/auth/logout", status: 204, validate: (value: unknown) => value === null },
  { method: "POST", path: "/admin/seasons/{season_id}/refresh", status: 200, validate: (value: unknown) => validSyncSummaryOut(value) },
  { method: "POST", path: "/admin/seasons/{season_id}/finish", status: 200, validate: (value: unknown) => validSeasonFinishedOut(value) },
  { method: "POST", path: "/admin/seasons/{season_id}/activate", status: 200, validate: (value: unknown) => validSeasonActivatedOut(value) },
  { method: "POST", path: "/admin/matches/{match_id}/result", status: 200, validate: (value: unknown) => validSettlementOut(value) },
  { method: "POST", path: "/auth/cheer", status: 200, validate: (value: unknown) => validCheerOut(value) },
  { method: "GET", path: "/auth/cheer", status: 200, validate: (value: unknown) => (validCheerOut(value) || value === null) },
  { method: "GET", path: "/auth/matches/{match_id}/context", status: 200, validate: (value: unknown) => validMatchContextOut(value) },
  { method: "POST", path: "/auth/bets", status: 200, validate: (value: unknown) => validBetOut(value) },
  { method: "GET", path: "/auth/bets", status: 200, validate: (value: unknown) => validBetPage(value) },
];
function validSuccessResponse(method: string, path: string, status: number, value: unknown): boolean {
  const parts = path.split('?')[0].split('/');
  const contract = responseContracts.find(row => {
    const expected = row.path.split('/');
    return row.method === method && expected.length === parts.length &&
      expected.every((part, index) => part.startsWith('{') || part === parts[index]);
  });
  return !!contract && contract.status === status && contract.validate(value);
}

export type MatchFilters = {
  season_id?: string; event_id?: string; status?: MatchOut['status']; limit?: number;
  offset?: number; api_source?: string; is_demo?: boolean; team_id?: string;
  date?: string; unscheduled?: boolean;
};
export type PublicResponseMetadata = {
  path: string; stale: boolean; ageSeconds: number | null; requestId: string | null;
};
export class LoLPickApiError extends Error {
  public outcomeUnknown = false;
  public status: number;
  public code: string;
  public requestId: string | null;
  public retryAfter: string | null;
  constructor(status: number, code: string, message: string,
    requestId: string | null, retryAfter: string | null) {
    super(message);
    this.name = 'LoLPickApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
  }
}
/** Keep UI commits synchronous. Invalidate on account/session change and component disposal. */
export function createAccountResponseGuard() {
  let generation = 0;
  return {
    changeAccount: () => { generation += 1; },
    async run<T>(operation: () => Promise<T>, apply: (value: T) => void, report?: (error: unknown) => void) {
      const started = generation;
      try {
        const value = await operation();
        if (generation !== started) return { kind: 'discarded' as const };
        apply(value);
        return { kind: 'applied' as const };
      } catch (error) {
        if (generation !== started) return { kind: 'discarded' as const };
        report?.(error);
        return { kind: 'failed' as const, error };
      }
    }
  };
}
/** Browser client. apiRoot ends with /api/v1. Tokens stay with your auth adapter. */
export function createLoLPickApi(options: {
  apiRoot: string; getAccessToken?: () => string | null | Promise<string | null>;
  fetcher?: typeof fetch; timeoutMs?: number; tokenTimeoutMs?: number;
  /** Stable account/session identity, never the rotating access token. */
  getAuthScope?: () => string | null;
  onPublicMetadata?: (metadata: PublicResponseMetadata) => void;
}) {
  const root = new URL(options.apiRoot, globalThis.location?.origin ?? 'http://localhost');
  if (!['http:', 'https:'].includes(root.protocol) || root.username || root.password || root.search || root.hash)
    throw new Error('Use a clean HTTP(S) API root.');
  if (root.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(root.hostname))
    throw new Error('Use HTTPS outside localhost.');
  if (!root.pathname.replace(/\/$/, '').endsWith('/api/v1'))
    throw new Error('API root must end with /api/v1.');
  const base = root.href.replace(/\/$/, '');
  const fetcher = options.fetcher ?? fetch;
  const httpBudget = options.timeoutMs ?? 15000;
  const tokenBudget = options.tokenTimeoutMs ?? 5000;
  if (![httpBudget, tokenBudget].every(v => Number.isFinite(v) && v > 0 && v <= 2147483647))
    throw new Error('Timeouts must be positive finite milliseconds within the timer range.');
  let authGeneration = 0;
  const inFlight = new Map<string, Promise<unknown>>(); // public, in-flight only; no persisted cache
  const query = (values: Record<string, string | number | boolean | undefined>) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) if (value !== undefined) params.set(key, String(value));
    params.sort(); const suffix = params.toString(); return suffix ? '?' + suffix : '';
  };
  const id = (value: string) => {
    if (typeof value !== 'string' || !value.trim() || value !== value.trim() ||
      value === '.' || value === '..' || /[\u0000-\u001f\u007f]/.test(value))
      throw new LoLPickApiError(0, 'client_invalid_id', '목록에서 받은 공개 ID를 사용하세요.', null, null);
    return encodeURIComponent(value);
  };
  async function execute<T>(method: 'GET' | 'POST', path: string, auth = false, body?: unknown, operationId?: string): Promise<T> {
    const headers: Record<string, string> = {};
    const generation = authGeneration;
    const scope = auth ? options.getAuthScope?.() : undefined;
    let dispatched = false;
    let requestId: string | null = null, retryAfter: string | null = null;
    const assertScope = () => {
      if (auth && (authGeneration !== generation || options.getAuthScope?.() !== scope)) {
        const error = new LoLPickApiError(0, 'client_auth_changed', '계정이 변경되어 이전 요청의 결과를 표시하지 않습니다.', requestId, retryAfter);
        error.outcomeUnknown = method === 'POST' && dispatched;
        throw error;
      }
    };
    // Snapshot the submitted body before waiting for the token provider.
    const encodedBody = body === undefined ? undefined : JSON.stringify(body);
    if (auth) {
      let tokenTimer: ReturnType<typeof setTimeout> | undefined;
      let token: string | null | undefined;
      try {
        token = await Promise.race([
          Promise.resolve().then(() => { assertScope(); return options.getAccessToken?.(); }),
          new Promise<never>((_, reject) => { tokenTimer = setTimeout(() => reject(
            new LoLPickApiError(0, 'client_token_timeout', '로그인 정보 조회 시간이 초과되었습니다.', null, null)
          ), tokenBudget); })
        ]);
      } catch (error) { assertScope(); throw error; }
      finally { clearTimeout(tokenTimer); }
      assertScope();
      if (!token) throw new LoLPickApiError(401, 'client_token_missing', 'Google 로그인 후 연결하세요.', null, null);
      headers.Authorization = 'Bearer ' + token;
    }
    if (operationId) headers['Idempotency-Key'] = operationId;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), httpBudget);
    try {
      assertScope();
      dispatched = true;
      const response = await fetcher(base + path, { method, headers,
        body: encodedBody, signal: timeout.signal,
        credentials: 'omit', redirect: 'error', cache: 'no-store' });
      requestId = response.headers.get('X-Request-ID');
      retryAfter = response.headers.get('Retry-After');
      assertScope();
      let content: unknown = null;
      let validJson = response.status === 204;
      try {
        if (response.status !== 204) {
          const text = await response.text();
          try { content = JSON.parse(text); validJson = true; } catch { /* nginx can return HTML errors */ }
        }
      } catch {
        // A failed error-body read must not hide an HTTP error we already received.
        if (response.ok) throw new LoLPickApiError(response.status, 'response_body_unavailable',
          '응답을 끝까지 받지 못했습니다. 쓰기 결과는 동일한 작업 키로 확인하세요.', requestId, retryAfter);
      }
      assertScope();
      if (!response.ok) {
        const detail = content && typeof content === 'object' && 'detail' in content
          ? (content as {detail?: {code?: unknown; message?: unknown}}).detail : undefined;
        throw new LoLPickApiError(response.status, typeof detail?.code === 'string' ? detail.code : 'http_error',
          typeof detail?.message === 'string' ? detail.message : '요청을 처리할 수 없습니다.',
          requestId, retryAfter);
      }
      if (!validJson) throw new LoLPickApiError(response.status, 'invalid_response',
        '서버 응답 형식이 올바르지 않습니다. 쓰기 결과는 동일한 작업 키로 확인하세요.', requestId, retryAfter);
      if (!validSuccessResponse(method, path, response.status, content))
        throw new LoLPickApiError(response.status, 'invalid_response',
          '응답 필드가 API 계약과 일치하지 않습니다. 쓰기 결과는 동일한 작업 키로 확인하세요.', requestId, retryAfter);
      if (!auth) {
        const age = response.headers.get('X-Data-Age-Seconds');
        const ageSeconds = age !== null && /^\d+$/.test(age) && Number.isSafeInteger(Number(age)) ? Number(age) : null;
        options.onPublicMetadata?.({path, stale: response.headers.get('X-Data-Stale') === '1', ageSeconds, requestId});
      }
      return content as T;
    } catch (error) {
      assertScope();
      const wrapped = error instanceof LoLPickApiError ? error : new LoLPickApiError(0,
        timeout.signal.aborted ? 'client_timeout' : 'client_network_error',
        '응답을 확인하지 못했습니다. 쓰기 요청은 원래 입력과 작업 키를 보존하세요.', requestId, retryAfter);
      if (method === 'POST' && dispatched && (wrapped.status === 0 || wrapped.status >= 500 ||
          wrapped.code === 'invalid_response' || wrapped.code === 'response_body_unavailable')) wrapped.outcomeUnknown = true;
      throw wrapped;
    } finally { clearTimeout(timer); }
  }
  function publicGet<T>(path: string): Promise<T> {
    const existing = inFlight.get(path); if (existing) return existing as Promise<T>;
    const promise = execute<T>('GET', path).finally(() => inFlight.delete(path));
    inFlight.set(path, promise); return promise;
  }
  const requiredKey = (key: string) => {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(key))
      throw new Error('Idempotency-Key must be a UUID. Keep the same key when retrying the same action.');
    return key;
  };
  return {
    /** Call BEFORE changing account/session or clearing private state. Does not roll back a dispatched write. */
    invalidateAuth: () => { authGeneration += 1; },
    health: () => publicGet<HealthOut>('/health'),
    ready: () => publicGet<ReadyOut>('/ready'),
    teams: (seasonId?: string) => publicGet<TeamOut[]>('/teams' + query({season_id: seasonId})),
    seasons: (year?: number) => publicGet<SeasonOut[]>('/seasons' + query({year})),
    events: (year?: number) => publicGet<SeasonOut[]>('/events' + query({year})),
    event: (eventId: string) => publicGet<EventOut>('/events/' + id(eventId)),
    matches: (filters: MatchFilters = {}) => publicGet<MatchPage>('/matches' + query(filters)),
    match: (matchId: string) => publicGet<MatchOut>('/matches/' + id(matchId)),
    stakeRanking: (matchId: string) => publicGet<StakeRankingOut>('/matches/' + id(matchId) + '/stake-ranking'),
    registerSession: (key: string) => execute<MemberOut>('POST', '/auth/session', true, undefined, requiredKey(key)),
    me: (seasonId?: string) => execute<MemberOut>('GET', '/auth/me' + query({season_id: seasonId}), true),
    logout: async () => { await execute<null>('POST', '/auth/logout', true); },
    cheer: (seasonId: string) => execute<CheerOut | null>('GET', '/auth/cheer' + query({season_id: seasonId}), true),
    chooseCheer: (body: CheerRequest, key: string) => execute<CheerOut>('POST', '/auth/cheer', true, body, requiredKey(key)),
    placeBet: (body: BetRequest, key: string) => execute<BetOut>('POST', '/auth/bets', true, body, requiredKey(key)),
    bets: (filters: {season_id?: string; limit?: number; offset?: number} = {}) =>
      execute<BetPage>('GET', '/auth/bets' + query(filters), true),
    matchContext: (matchId: string) => execute<MatchContextOut>('GET', '/auth/matches/' + id(matchId) + '/context', true),
    // Server verifies DB member type + MFA. Never infer permissions from UI/user_metadata.
    admin: {
      refresh: (seasonId: string) => execute<SyncSummaryOut>('POST', '/admin/seasons/' + id(seasonId) + '/refresh', true),
      activate: (seasonId: string) => execute<SeasonActivatedOut>('POST', '/admin/seasons/' + id(seasonId) + '/activate', true),
      finish: (seasonId: string) => execute<SeasonFinishedOut>('POST', '/admin/seasons/' + id(seasonId) + '/finish', true),
      result: (matchId: string, body: MatchResultRequest) =>
        execute<SettlementOut>('POST', '/admin/matches/' + id(matchId) + '/result', true, body),
    }
  };
}

