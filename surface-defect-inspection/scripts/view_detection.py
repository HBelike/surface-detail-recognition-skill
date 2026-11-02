"""逐视角检测后合并图像证据，不以投票否定单视角清晰异常。"""
from domain import Inspection
from imaging import prepare_images
from vision_client import analyze,AnalysisFailure


def detect_views(paths,model,base_url,timeout,thinking):
    results=[];all_entries=[];per_view=[]
    usage={k:0 for k in ('prompt_tokens','completion_tokens','total_tokens')}
    calls=0;unknown=0
    for path in paths:
        entries=prepare_images([path],'crops')
        try:
            result,meta=analyze(entries,model,base_url,timeout,thinking)
        except AnalysisFailure as exc:
            failed=dict(exc.metadata)
            failed['usage']={k:usage[k]+exc.metadata.get('usage',{}).get(k,0) for k in usage}
            failed['request_count']=calls+exc.metadata.get('request_count',0)
            failed['requests_with_unknown_usage']=unknown+exc.metadata.get('requests_with_unknown_usage',0)
            failed['completed_view_metadata']=per_view
            raise AnalysisFailure(str(exc),failed) from None
        results.append(result);all_entries.extend(entries);per_view.append({'source':path.name,**meta})
        for k in usage:usage[k]+=meta.get('usage',{}).get(k,0)
        calls+=meta.get('request_count',0);unknown+=meta.get('requests_with_unknown_usage',0)
    states={}
    for category in ('linear_trace','material_loss'):
        votes=[getattr(r,category) for r in results]
        states[category]='present' if 'present' in votes else 'uncertain' if 'uncertain' in votes else 'not_observed'
    result=Inspection(**states,findings=[f for r in results for f in r.findings],
                      observations=[o for r in results for o in r.observations],
                      limitations=list(dict.fromkeys([n for r in results for n in r.limitations]+['逐视角检查后合并；其他视角未见不能否定单视角清晰证据。'])))
    return result,{'usage':usage,'request_count':calls,'requests_with_unknown_usage':unknown,
                   'per_view':per_view,'response_models':list(dict.fromkeys(m.get('response_model') for m in per_view)),
                   'thinking':thinking,'detection_policy':'per_view'},all_entries
