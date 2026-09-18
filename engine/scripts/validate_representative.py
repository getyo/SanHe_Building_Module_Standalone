from pathlib import Path
import argparse,json,hashlib,sys,importlib.util
import numpy as np,shapely
from shapely.geometry import shape,Polygon,LineString,Point
from shapely.ops import unary_union
R=Path(__file__).resolve().parents[2];O=R/'outputs';sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--scope',choices=['representative','full'],default='representative');args=parser.parse_args()
CFG=json.loads((R/'run_config.json').read_text(encoding='utf-8'))
def input_path(key):return (R/CFG['inputs'][key]).resolve()
sp=importlib.util.spec_from_file_location('mc',input_path('map_common'));mc=importlib.util.module_from_spec(sp);sp.loader.exec_module(mc)
allowed=shape(json.loads((O/'audit/label40.geojson').read_text()));spec=json.loads((O/'specs'/f'{args.scope}_compiled.json').read_text());h,w=spec['label_shape'];f=O/'geometry'/args.scope/f'{args.scope}_details.obj';vs=[];uvs=[];ns=[];faces=[];obj='';mat='';invalid=[];objects={}
for l in f.read_text().splitlines():
 if l.startswith('v '):vs.append(list(map(float,l.split()[1:])))
 elif l.startswith('vt '):uvs.append(list(map(float,l.split()[1:])))
 elif l.startswith('vn '):ns.append(list(map(float,l.split()[1:])))
 elif l.startswith('o '):obj=l[2:];objects[obj]=[]
 elif l.startswith('usemtl '):mat=l[7:]
 elif l.startswith('f '):
  q=[tuple(int(t)-1 for t in x.split('/')) for x in l.split()[1:]]
  if any(a<0 or a>=len(vs) or b<0 or b>=len(uvs) or c<0 or c>=len(ns) for a,b,c in q):invalid.append({'object':obj,'type':'index'})
  faces.append((obj,mat,q));objects[obj].append(len(faces)-1)
v=np.array(vs);outside=[];areas=[];normals=[]
for i,(obj,mat,q) in enumerate(faces):
 t=v[[x[0] for x in q]];px=np.column_stack(((t[:,0]/mc.UE_PER_PX10+w/2)/mc.SCALE,(h/2-t[:,1]/mc.UE_PER_PX10)/mc.SCALE));p=Polygon(px)
 if p.area==0:p=LineString(px) if len(set(map(tuple,px)))>1 else Point(px[0])
 if not allowed.covers(p):outside.append({'face':i,'id':obj,'outside_area_px1':p.difference(allowed).area})
 cross=np.cross(t[1]-t[0],t[2]-t[0]);area=np.linalg.norm(cross)*.5;areas.append(area)
 if area<=1e-8:invalid.append({'face':i,'id':obj,'type':'zero_area'})
 if area>0 and np.dot(cross/np.linalg.norm(cross),np.array(ns[q[0][2]]))<.999:invalid.append({'face':i,'id':obj,'type':'normal_winding'})
# Compare exported cap footprints with exact compiled targets; top faces only.
cap=[]
for s in spec['extrusions']:
 ps=[]
 for i in objects.get(s['id'],[]):
  t=v[[x[0] for x in faces[i][2]]]/100
  if np.max(abs(t[:,2]-s['z_top_m']))<1e-5:ps.append(Polygon(t[:,:2]))
 target=unary_union([Polygon(np.array(s['xy_m'])[idx]) for idx in s['top_triangles']]);actual=unary_union(ps);cap.append({'id':s['id'],'excess_m2':actual.difference(target).area,'missing_m2':target.difference(actual).area,'symmetric_difference_m2':actual.symmetric_difference(target).area,'boundary_hausdorff_m':actual.boundary.hausdorff_distance(target.boundary)})
 if actual.symmetric_difference(target).area>1e-4:invalid.append({'id':s['id'],'type':'exported cap mismatch'})
sourcehash=hashlib.sha256(input_path('base_obj').read_bytes()).hexdigest();audit=json.loads((O/'audit/input_audit.json').read_text());mesh=json.loads((O/'reports'/f'{args.scope}_mesh_precheck.json').read_text());sha=hashlib.sha256(f.read_bytes()).hexdigest()
result={'stage':3,'status':'PASS' if not invalid and not outside and not mesh['issues'] else 'FAIL','model_sha256':sha,'triangles':len(faces),'objects':len(objects),'vertices':len(v),'aabb_cm':[v.min(axis=0).tolist(),v.max(axis=0).tolist()],'invalid':invalid,'label_violations':outside,'label_method':'Every actual exported triangle full XY projection covered by exact original Label40 raster polygons; zero pixel tolerance','mesh_precheck_issues':mesh['issues'],'original_source_unchanged':sourcehash==audit['base_obj']['sha256'],'source_faces_not_exported':not any(x=='terrain_building' for x in objects),'material_slots_used':sorted(set(mat for _,mat,_ in faces)),'exported_cap_tests':cap,'UE_alignment':'file-side coordinate checks only; actual UE import pending'}
(O/'reports'/f'{args.scope}_geometry_report.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='exported_cap_tests'}))
