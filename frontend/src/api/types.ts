// Aliases over the types generated from the FastAPI OpenAPI document
// (src/api/schema.d.ts, `npm run gen:api`). Never hand-edit shapes here:
// change the Pydantic response model, re-export openapi.json, regenerate.
import type { components } from './schema'

type S = components['schemas']

export type Overview = S['OverviewOut']
export type LlmUsageSummary = S['LlmUsageSummary']
export type CampaignListItem = S['CampaignListItem']
export type CampaignList = S['CampaignListOut']
export type CampaignDetail = S['CampaignDetailOut']
export type CampaignDetailCampaign = S['CampaignDetailCampaign']
export type CampaignRules = S['CampaignRulesOut']
export type McAsset = S['McAssetOut']
export type McClip = S['McClipOut']
export type McJob = S['McJobOut']
export type JobRecent = S['JobRecentOut']
export type JobRecentItem = S['JobRecentItem']
export type Pipeline = S['PipelineOut']
export type Videos = S['VideosOut']
export type Clips = S['ClipsOut']
export type ClipInventoryItem = S['ClipInventoryItem']
export type PipelineError = S['PipelineError']

export type CampaignStatus = CampaignListItem['status']
export type CampaignSource = CampaignListItem['source_provider']
export type AssetStatus = McAsset['status']
export type JobStatus = McJob['status']
export type ClipStatus = McClip['status']
export type ClipQAStatus = McClip['qa_status']
