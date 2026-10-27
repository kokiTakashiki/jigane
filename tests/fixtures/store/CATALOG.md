# 保存物の素材の目録

対象はごみ箱と STAGING を除く 413 レビュー（直下は review.json を持つ owner/repo/<review> の 3 階層。local/shop/p を含む）。

写し方: 形の「タグ」（鍵の集合、列挙の値、空か否か）を貪欲に被覆するよう、ファイルの小さい順に、未被覆のタグを持つものだけ採った。各サンプルのディレクトリ owner/repo/<review>/ には、対象ファイルに加えて読み口が要る review.json・map.json・gates.json・background.json・approval.json を同じ置き換えで添えた。形ごとの「他との違い」は、そのサンプルが初めて被覆したタグ（鍵の差・列挙値）で示す。

注意: themes/ は空ディレクトリのため素材なし。.lock、.trash、archived（空のファイル）、trees/、changes.diff は対象外（changes.diff と trees/ は JSON でなく、コードの中身を含むため写さない）。

## approval.json（89 件、サンプル 1 形）

承認の制約を外した後の読み口は approval.json を読まず、書かない。この素材は、置き場に残った approval.json が他のファイルの読みを妨げないことを縛るために残す。

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| approval/s01/owner/repo/1 | approval empty |

## background.json（336 件、サンプル 5 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| background/s01/owner/repo/@sample | keys:documents,issues,owners,past_changes; owners nonempty:owners,path |
| background/s02/owner/repo/@sample | keys:documents,issues,owners,past_pull_requests |
| background/s03/owner/repo/@sample | past_changes nonempty:files,kind,sha,title |
| background/s04/owner/repo/1 | past_changes nonempty:files,kind,number,title |
| background/s05/owner/repo/1 | documents nonempty:str; past_pull_requests nonempty:files,number,title |

## bot_comments.json（13 件、サンプル 2 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| bot_comments/s01/owner/repo/1 | keys:comments,unavailable |
| bot_comments/s02/owner/repo/1 | comment keys:body,html_url,id,judged,original_commit_id,original_line,original_start_line,path,region_keys,touched_lines,user |

## findings.json（409 件、サンプル 32 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| findings/s01/owner/repo/1 | groups:macro; top:groups,last_number,last_theme_number,stale_groups,themes |
| findings/s02/owner/repo/sample | groups:security |
| findings/s03/owner/repo/@sample | groups:macro,micro |
| findings/s04/owner/repo/1 | stale_groups nonempty |
| findings/s05/owner/repo/@sample | groups:macro,micro,security |
| findings/s06/owner/repo/@sample | adjudication:None; group=macro; item keys:adjudication,body,claim,declaration_ref,evidence,group,id,judgment_region,references,severity,theme,triage,viewpoint; severity=nit; triage=remaining |
| findings/s07/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,discard_kind,evidence,group,id,judgment_region,references,severity,theme,triage,viewpoint |
| findings/s08/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,evidence,group,id,judgment_region,nit_reason,references,severity,theme,triage,viewpoint; nit_reason keys:concern,kind,text; nit_reason.kind=unverifiable_from_diagram |
| findings/s09/owner/repo/@sample | item keys:adjudication,body,claim,concern,declaration_ref,discard_kind,evidence,group,id,judgment_region,references,severity,theme,triage,viewpoint |
| findings/s10/owner/repo/@sample | evidence nonempty; severity=warning; triage=must_read |
| findings/s11/owner/repo/@sample | group=micro; theme keys:finding_ids,id,severity,title,triage; theme set; theme.triage=folded; triage=folded |
| findings/s12/owner/repo/@sample | adj.kind=rebuttal verdict=weakened; adjudication:challenge,kind,reason,recheck,verdict |
| findings/s13/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,evidence,from_concern,group,id,judgment_region,references,severity,theme,triage,viewpoint |
| findings/s14/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,evidence,group,id,judgment_region,out_of_scope,references,severity,theme,triage,viewpoint; out_of_scope; triage=out_of_scope |
| findings/s15/owner/repo/@sample | references nonempty |
| findings/s16/owner/repo/@sample | nit_reason.kind=not_a_contract; nit_reason.kind=other |
| findings/s17/owner/repo/@sample | nit_reason.kind=decided_by_declaration |
| findings/s18/owner/repo/@sample | adj.kind=rebuttal verdict=rejected; groups:micro; triage=rejected |
| findings/s19/owner/repo/@sample | adj.kind=rebuttal verdict=kept |
| findings/s20/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,deepens,evidence,group,id,judgment_region,references,severity,theme,triage,viewpoint |
| findings/s21/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,deepens,evidence,group,id,judgment_region,out_of_scope,references,severity,theme,triage,viewpoint |
| findings/s22/owner/repo/sample | top:groups,last_number; triage=None |
| findings/s23/owner/repo/@sample | adjudication:challenge,kind,reason,recheck,tool_claim,verdict,verified; theme.triage=must_read |
| findings/s24/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,evidence,group,id,judgment_region,references,severity,theme,triage,triage_reason,viewpoint; theme.triage=remaining |
| findings/s25/owner/repo/@sample | nit_reason.kind=settled_earlier |
| findings/s26/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,evidence,from_concern,group,id,judgment_region,references,severity,theme,triage,triage_reason,viewpoint |
| findings/s27/owner/repo/@sample | item keys:adjudication,body,change_reason,claim,declaration_ref,evidence,group,id,judgment_region,previous,previous_verified,references,severity,theme,triage,viewpoint |
| findings/s28/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,evidence,from_concern,group,id,judgment_region,nit_reason,references,severity,theme,triage,viewpoint |
| findings/s29/owner/repo/@sample | nit_reason.kind=deepening |
| findings/s30/owner/repo/@sample | group=security |
| findings/s31/owner/repo/sample | triage=deferred |
| findings/s32/owner/repo/sample | adj.kind=alternative verdict=rejected; adjudication:alternative,defense,kind,reason,verdict; severity=critical |

## findings_history.json（26 件、サンプル 9 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| findings_history/s01/owner/repo/1 | groups:macro; row keys:findings,ran_at |
| findings_history/s02/owner/repo/@sample | groups:micro |
| findings_history/s03/owner/repo/1 | row keys:discard,findings,ran_at |
| findings_history/s04/owner/repo/@sample | adj:null; item keys:adjudication,body,claim,declaration_ref,evidence,group,id,judgment_region,references,severity,theme,triage,viewpoint |
| findings_history/s05/owner/repo/@sample | item keys:adjudication,body,claim,concern,declaration_ref,discard_kind,evidence,group,id,judgment_region,references,severity,theme,triage,viewpoint; item keys:adjudication,body,claim,declaration_ref,discard_kind,evidence,group,id,judgment_region,references,severity,theme,triage,viewpoint |
| findings_history/s06/owner/repo/@sample | item keys:adjudication,body,claim,declaration_ref,evidence,group,id,judgment_region,out_of_scope,references,severity,theme,triage,viewpoint |
| findings_history/s07/owner/repo/1 | adj:challenge,kind,reason,recheck,verdict; groups:macro,micro,security; item keys:adjudication,body,claim,declaration_ref,evidence,group,id,judgment_region,nit_reason,references,severity,theme,triage,viewpoint |
| findings_history/s08/owner/repo/1 | groups:macro,micro |
| findings_history/s09/owner/repo/+sample | item keys:adjudication,body,claim,declaration_ref,deepens,evidence,group,id,judgment_region,references,severity,theme,triage,viewpoint |

## gates.json（412 件、サンプル 4 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| gates/s01/owner/repo/1 | keys:keys,next_violation,next_warning,violations,warnings |
| gates/s02/owner/repo/@sample | keys:format,keys,next_violation,next_warning,violations,warnings |
| gates/s03/owner/repo/@sample | keys nonempty; violations nonempty:cause,confidence,detector_note,excerpt,id,legitimate,line,number,path,reason,remedy,source |
| gates/s04/owner/repo/1 | warnings nonempty:cause,confidence,detector_note,excerpt,id,legitimate,line,number,path,reason,remedy,source |

## map.json（413 件、サンプル 21 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| map/s01/owner/repo/@sample | line_counts keys:behavior_removal,bot_response,comment_only,dependency_build,format_only,generated,internal_interface,logic,public_interface,sensitive_logic,test; line_counts keys:confirm,machine,required; mode=diff; regions empty; seg keys:bot_comment_ids,declaration_kind,kind,level,new_line_count,new_path,new_span,old_line_count,old_path,old_span,qualified_name,visibility; seg.change_kind=ABSENT; seg.decl=None; seg.kind=test; seg.level=confirm; seg.vis=None; top:line_counts,mode,regions,segments |
| map/s02/owner/repo/@sample | seg.kind=logic |
| map/s03/owner/repo/sample | mode=feature |
| map/s04/owner/repo/@sample | seg keys:bot_comment_ids,change_kind,declaration_kind,kind,level,new_line_count,new_path,new_span,old_line_count,old_path,old_span,qualified_name,visibility; seg.change_kind=added |
| map/s05/owner/repo/@sample | seg.change_kind=changed; seg.decl=function; seg.vis=public |
| map/s06/owner/repo/@sample | seg.decl=method; seg.vis=file |
| map/s07/owner/repo/@sample | seg.decl=variable |
| map/s08/owner/repo/@sample | region keys:bot_comment_ids,content_hash,id,key,kind,line_count,new_span,old_path,old_span,path,qualified_name,summary; regions nonempty; seg.kind=dependency_build; seg.level=required |
| map/s09/owner/repo/@sample | seg.kind=format_only; seg.level=machine |
| map/s10/owner/repo/@sample | seg.decl=class |
| map/s11/owner/repo/@sample | seg.change_kind=removed |
| map/s12/owner/repo/@sample | seg.kind=public_interface |
| map/s13/owner/repo/@sample | seg.kind=comment_only |
| map/s14/owner/repo/@sample | seg.kind=behavior_removal |
| map/s15/owner/repo/1 | seg.kind=internal_interface; seg.vis=internal |
| map/s16/owner/repo/@sample | seg.kind=generated |
| map/s17/owner/repo/sample | line_counts keys:behavior_removal,bot_response,comment_only,dependency_build,format_only,generated,logic,public_interface,sensitive_logic,test; region keys:bot_comment_ids,content_hash,id,judged,key,kind,line_count,new_span,old_span,path,qualified_name,summary; seg keys:bot_comment_ids,declaration_kind,kind,level,new_line_count,new_path,new_span,old_line_count,old_path,old_span,qualified_name; seg.vis=ABSENT |
| map/s18/owner/repo/@sample | seg.new_path null |
| map/s19/owner/repo/1 | region keys:bot_comment_ids,content_hash,id,judged,key,kind,line_count,new_span,old_path,old_span,path,qualified_name,summary |
| map/s20/owner/repo/1 | seg.bot_comment_ids nonempty; seg.kind=bot_response |
| map/s21/owner/repo/sample | region keys:content_hash,id,judged,key,kind,line_count,new_span,old_span,path,qualified_name,summary; seg keys:declaration_kind,kind,level,new_line_count,new_path,new_span,old_line_count,old_path,old_span,qualified_name |

## pairs.json（336 件、サンプル 2 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| pairs/s01/owner/repo/@sample | null in pairs; pairs nonempty |
| pairs/s02/owner/repo/1 | no null |

## pr_state.json（13 件、サンプル 3 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| pr_state/s01/owner/repo/1 | keys:fetched_at,state; state=open |
| pr_state/s02/owner/repo/1 | state=draft |
| pr_state/s03/owner/repo/1 | state=merged |

## regions.json（413 件、サンプル 4 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| regions/s01/owner/repo/1 | keys:next_number,regions |
| regions/s02/owner/repo/@sample | region keys:content_hash,id |
| regions/s03/owner/repo/sample | region keys:content_hash,id,judged |
| regions/s04/owner/repo/@sample | region keys:content_hash,gone,id |

## review.json（413 件、サンプル 16 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| review/s01/owner/repo/sample | declaration keys:excerpts,intent,source,source_label,warnings; declaration.intent null; declaration.source=excerpts; keys:background=null,content_warnings=arr,created_at=str,declaration=obj,diff_source=null,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=null,mode=str,paths=arr,pull_request=null,refreshed_at=null,repository_root=str,targets=arr,warnings=arr; kind=feature; mode=feature; warnings nonempty |
| review/s02/owner/repo/sample | declaration keys:excerpts,intent,source; declaration.source=intent; keys:background=null,created_at=str,declaration=obj,id=str,mode=str,paths=arr,pull_request=null,refreshed_at=null,repository_root=str,targets=arr,warnings=arr; kind=ABSENT |
| review/s03/owner/repo/+sample | design_doc keys:code_version,document_path,document_version,selection; keys:background=null,content_warnings=arr,created_at=str,declaration=obj,design_doc=obj,diff_source=null,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=null,mode=str,paths=arr,pull_request=null,refreshed_at=str,repository_root=str,targets=null,warnings=arr; kind=design_doc |
| review/s04/owner/repo/@sample | diff_source keys:base_sha,base_tree,head_sha,merge_base; keys:background=obj,content_warnings=arr,created_at=str,declaration=obj,diff_source=obj,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=obj,mode=str,paths=arr,pull_request=null,refreshed_at=null,repository_root=str,targets=null,warnings=arr; kind=range; local_range keys:range_text,working_directory; mode=diff |
| review/s05/owner/repo/sample | declaration.excerpts nonempty |
| review/s06/owner/repo/@sample | keys:background=obj,content_warnings=arr,created_at=str,declaration=obj,diff_source=obj,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=obj,mode=str,paths=arr,pull_request=null,refreshed_at=str,repository_root=str,targets=null,warnings=arr |
| review/s07/owner/repo/+sample | content_warnings nonempty; keys:background=null,content_warnings=arr,created_at=str,declaration=obj,design_doc=obj,diff_source=null,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=null,mode=str,paths=arr,pull_request=null,refreshed_at=null,repository_root=str,targets=null,warnings=arr |
| review/s08/owner/repo/sample | keys:background=null,created_at=str,declaration=obj,diff_source=null,id=str,is_diff=bool,is_pull_request=bool,kind=str,mode=str,paths=arr,pull_request=null,refreshed_at=null,repository_root=str,targets=arr,warnings=arr |
| review/s09/owner/repo/@sample | keys:background=obj,content_warnings=arr,created_at=str,declaration=obj,design_doc=null,diff_source=obj,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=obj,mode=str,paths=arr,pull_request=null,refreshed_at=null,repository_root=str,targets=null,warnings=arr |
| review/s10/owner/repo/1 | declaration.source=pull_request; keys:background=obj,created_at=str,declaration=obj,id=str,mode=str,paths=arr,pull_request=obj,refreshed_at=str,repository_root=str,targets=null,warnings=arr; pull_request keys:base_sha,base_tree,head_sha,linked_issues,merge_base,number,title,url |
| review/s11/owner/repo/1 | keys:background=obj,content_warnings=arr,created_at=str,declaration=obj,diff_source=obj,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=null,mode=str,paths=arr,pull_request=obj,refreshed_at=null,repository_root=str,targets=null,warnings=arr; kind=pull_request; pull_request keys:linked_issues,number,title,url |
| review/s12/owner/repo/1 | keys:background=obj,content_warnings=arr,created_at=str,declaration=obj,diff_source=obj,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=null,mode=str,paths=arr,pull_request=obj,refreshed_at=str,repository_root=str,targets=null,warnings=arr |
| review/s13/owner/repo/1 | keys:background=obj,content_warnings=arr,created_at=str,declaration=obj,design_doc=null,diff_source=obj,id=str,is_diff=bool,is_pull_request=bool,kind=str,local_range=null,mode=str,paths=arr,pull_request=obj,refreshed_at=str,repository_root=str,targets=null,warnings=arr |
| review/s14/owner/repo/1 | keys:background=obj,created_at=str,declaration=obj,id=str,mode=str,paths=arr,pull_request=obj,refreshed_at=null,repository_root=str,targets=null,warnings=arr |
| review/s15/owner/repo/sample | keys:created_at=str,declaration=obj,id=str,mode=str,paths=arr,repository_root=str,warnings=arr |
| review/s16/owner/repo/sample | keys:background=null,created_at=str,declaration=obj,id=str,mode=str,paths=arr,pull_request=null,refreshed_at=str,repository_root=str,targets=arr,warnings=arr |

## run_in_progress.json（286 件、サンプル 1 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| run_in_progress/s01/owner/repo/1 | keys: |

## runs.json（409 件、サンプル 26 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| runs/s01/owner/repo/sample | groups:macro; macro inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason; macro inv role=discovery; macro keys:discovered,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,invocations,model,ran_at |
| runs/s02/owner/repo/@sample | macro inv role=diagram; macro keys:discovered,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,elapsed_s,invocations,model,ran_at,shape; macro shape=diagram |
| runs/s03/owner/repo/sample | groups:security; security inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,role,split_index,terminal_reason; security inv role=discovery; security keys:discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,unverified_candidates |
| runs/s04/owner/repo/1 | macro keys:discarded,discovered,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,elapsed_s,invocations,model,ran_at,shape |
| runs/s05/owner/repo/@sample | macro conclude; macro inv role=single; macro keys:conclude,discovered,discovered_by_perspective,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,elapsed_s,failed_perspectives,invocations,merged_duplicates,model,ran_at,shape |
| runs/s06/owner/repo/sample | macro inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,role,split_index,terminal_reason; macro inv role=judge; macro inv role=rebuttal; macro keys:discovered,dropped_invalid_schema,dropped_mode_mismatch,dropped_without_evidence,failed_splits,invocations,model,ran_at,unverified_candidates |
| runs/s07/owner/repo/+sample | macro inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_source,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason; macro inv role=design; macro keys:discarded,discovered,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,elapsed_s,invocations,model,ran_at,record_kind,shape; macro record_kind=succeeded; macro shape=design |
| runs/s08/owner/repo/@sample | groups:macro,micro; micro inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_source,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason; micro inv role=discovery; micro keys:budget_stops,discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,elapsed_s,failed_splits,invocations,launch_failures,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,theme_bundling,unverified_candidates |
| runs/s09/owner/repo/@sample | groups:micro; micro inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason; micro inv role=judge; micro inv role=rebuttal |
| runs/s10/owner/repo/@sample | macro inv role=discovery:purpose; macro inv role=discovery:route; macro inv role=discovery:verify; macro keys:conclude,discovered,discovered_by_perspective,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,elapsed_s,failed_perspectives,invocations,merged_duplicates,model,ran_at |
| runs/s11/owner/repo/@sample | macro inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_source,cost_usd,duration_ms,input_tokens,is_error,max_budget_usd,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason; micro inv keys:cache_creation_input_tokens,cache_read_input_tokens,concerns,cost_source,cost_usd,duration_ms,input_tokens,is_error,max_budget_usd,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason; micro keys:budget_stops,discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,elapsed_s,failed_splits,invocations,launch_failures,mcp_servers,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,theme_bundling,unverified_candidates |
| runs/s12/owner/repo/@sample | micro inv role=themes; micro keys:budget_stops,discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,elapsed_s,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,theme_bundling,unverified_candidates; micro theme_bundling obj |
| runs/s13/owner/repo/sample | macro inv role=conclude; macro keys:conclude,discovered,discovered_by_perspective,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,failed_perspectives,invocations,merged_duplicates,model,ran_at |
| runs/s14/owner/repo/@sample | micro inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_source,cost_usd,duration_ms,input_tokens,is_error,max_budget_usd,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason |
| runs/s15/owner/repo/@sample | groups:macro,micro,security; security inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason; security keys:budget_stops,discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,elapsed_s,failed_splits,invocations,launch_failures,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,unverified_candidates |
| runs/s16/owner/repo/1 | micro keys:discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,theme_bundling,unverified_candidates |
| runs/s17/owner/repo/@sample | micro launch_failures nonempty |
| runs/s18/owner/repo/@sample | security inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_source,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason |
| runs/s19/owner/repo/@sample | security inv role=judge; security inv role=rebuttal; security keys:budget_stops,discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,elapsed_s,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,unverified_candidates |
| runs/s20/owner/repo/@sample | macro keys:discarded,discovered,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,elapsed_s,invocations,launch_failures,model,ran_at,record_kind,shape; macro launch_failures nonempty |
| runs/s21/owner/repo/sample | macro keys:discovered,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,failed_splits,invocations,model,ran_at,unverified_candidates; micro inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_usd,duration_ms,input_tokens,is_error,num_turns,output_tokens,permission_denials,role,split_index,terminal_reason; micro keys:deferred,discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,theme_bundling,unverified_candidates |
| runs/s22/owner/repo/1 | security inv keys:cache_creation_input_tokens,cache_read_input_tokens,cost_source,cost_usd,duration_ms,input_tokens,is_error,max_budget_usd,num_turns,output_tokens,permission_denials,prompt_chars,prompt_version,role,split_index,terminal_reason |
| runs/s23/owner/repo/sample | micro deferred |
| runs/s24/owner/repo/sample | macro keys:discovered,dropped_invalid_schema,dropped_without_bot_findings,dropped_without_evidence,effort,failed_splits,invocations,model,ran_at,unverified_candidates; micro keys:deferred,discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,theme_bundling,unverified_candidates; security keys:discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,unverified_candidates |
| runs/s25/owner/repo/1 | micro keys:discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,elapsed_s,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,theme_bundling,unverified_candidates; security keys:discovered,dropped_invalid_schema,dropped_outside_shard,dropped_without_evidence,effort,elapsed_s,failed_splits,invocations,model,ran_at,reviewed_files,shards,succeeded_shards,target_files,unverified_candidates |
| runs/s26/owner/repo/sample | macro inv role=alternative; macro inv role=defense |

## selection.json（111 件、サンプル 4 形）

| 素材の識別子 | このサンプルが初めて被覆したタグ |
|---|---|
| selection/s01/owner/repo/sample | keys:held,objections,picked |
| selection/s02/owner/repo/1 | picked nonempty |
| selection/s03/owner/repo/@sample | held nonempty |
| selection/s04/owner/repo/@sample | objections nonempty |

## 置き場の根

| 写した先 | 元 | 内容 |
|---|---|---|
| root/mcp/mcp.json | mcp.json | mcpServers の各 command と args を架空のパスへ置換 |
| themes/ | themes/ | 空。素材なし |

## 置き換えた欄

- パス（path、old_path、new_path、document_path、paths[]、targets[]、documents[]、files[]、pairs の各要素、line_counts の鍵、regions の鍵のパス部、gates.keys の鍵のパス部、design_doc.selection[]）: src/f<N>.<元の拡張子>。同一レビュー内で一貫。
- qualified_name と regions の鍵の中間部: Sym<N>（空文字は空のまま）。
- sha、base_sha、head_sha、merge_base、base_tree、content_hash、original_commit_id、code_version、document_version: sha256 から作った同じ桁の別の 16 進。同一値は同一値に写る。
- review の id: owner/repo#1 または owner/repo:@sample / +sample / sample（元の先頭の @ と + は残した）。ディレクトリ名も同じ。
- repository_root、working_directory: /tmp/sample-repo。range_text: main..HEAD。user: user。url: `https://example.invalid/pull/1`。html_url: `https://example.invalid/c/1`。title、source_label: title、label。
- background.owners[].owners[]: owner。micro.mcp_servers[]: server。
- それ以外の文字列（claim、body、challenge、reason、summary、intent、text、excerpt、remedy、detector_note、legitimate、source の長文、objections の値、concerns など）: "text"。
- 残したもの: 鍵、数値、真偽、null、列挙（kind、level、severity、triage、group、viewpoint、mode、state、change_kind、declaration_kind、visibility、verdict、role、shape、record_kind、cost_source、terminal_reason、perspective、confidence、cause など）、F1 や J2 のような短い識別子、ISO 時刻、モデル名、prompt_version。

## 読み口が要る他のファイル

- read_review_at（review.py:1633）は review.json と map.json を必須とし、background.json、gates.json を load_background、load_gates で読む（approval.json は承認の制約を外してから読まない）。各サンプルにはこの 5 つを添えた（元に無いファイルは添えていない）。
- regions.json の鍵（path::qualified::kind）と map.json の regions[].key は同じ置き換えで揃えた。findings の evidence.path と map の path、pairs.json の要素、gates の鍵のパス部も同一の対応表で揃えた。ただし対象ファイル以外（たとえば findings.json のサンプルに添えない regions.json）は含まれず、突き合わせの検査には元のレビューが持つ regions.json などを別途添える必要がある。
- review.json の diff_source、pull_request、local_range、targets、design_doc の組は read_stored_sources と _origin_from_stored が整合を検査するため、構造（null か否か）は元のまま残した。
