import argparse,json,math,sys,hashlib,shutil
from pathlib import Path
import numpy as np,shapely
from shapely.geometry import shape,mapping,Polygon,Point,LineString,box
from shapely.ops import unary_union,transform
from shapely.strtree import STRtree
from PIL import Image,ImageDraw
R=Path(__file__).resolve().parents[2];O=R/'outputs';sys.dont_write_bytecode=True
for folder in ('specs','reports','geometry'):(O/folder).mkdir(parents=True,exist_ok=True)
parser=argparse.ArgumentParser();parser.add_argument('--scope',choices=['representative','full'],default='representative');args=parser.parse_args()
CFG=json.loads((R/'run_config.json').read_text(encoding='utf-8'))
def input_path(key):return (R/CFG['inputs'][key]).resolve()
import importlib.util
sp=importlib.util.spec_from_file_location('mc',input_path('map_common'));mc=importlib.util.module_from_spec(sp);sp.loader.exec_module(mc)
M=mc.PX_M;tasks=json.loads((O/'specs/tasks.json').read_text(encoding='utf-8'));h,w=tasks['label_shape']
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
def toworld(p):return transform(lambda x,y:(mc.ue_x_of_col(np.array(x)*mc.SCALE,w)/100,mc.ue_y_obj_of_row(np.array(y)*mc.SCALE,h)/100),p)
def xy(origin,angle,x,y):
 a=math.radians(angle);return [origin[0]+(math.cos(a)*x+math.sin(a)*y)/M,origin[1]+(math.sin(a)*x-math.cos(a)*y)/M]
def localpoly(b,q):
 x0,y0,x1,y1=q;return toworld(Polygon([xy(b['origin_px1'],b['angle_image_deg'],x,y) for x,y in [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]]))
# Exact original triangle footprints and height planes (no point-only approximation).
verts=[];faces=[]
with input_path('base_obj').open(encoding='utf-8-sig') as f:
 for l in f:
  if l.startswith('v '):verts.append(list(map(float,l.split()[1:4])))
  elif l.startswith('f '):faces.append([int(s.split('/')[0])-1 for s in l.split()[1:]])
v=np.array(verts)/100;tri=v[np.array(faces)];polys=shapely.polygons(tri[:,:,:2]);valid=shapely.area(polys)>1e-12;tri=tri[valid];polys=polys[valid];tree=STRtree(polys)
A=np.concatenate([tri[:,:,:2],np.ones((len(tri),3,1))],axis=2);planes=np.linalg.solve(A,tri[:,:,2,None])[:,:,0]
foundation=[]
def fit(p,ident):
 ids=tree.query(p,predicate='intersects');cuts=shapely.intersection(polys[ids],p);non=shapely.area(cuts)>1e-13;ids=ids[non];cuts=cuts[non];cover=unary_union(cuts);gap=p.difference(cover).area
 if gap>1e-7:raise RuntimeError(f'{ident}: original foundation missing {gap:.9f} m2')
 zs=[]
 for i,g in zip(ids,cuts):
  pts=shapely.get_coordinates(g);zs.extend((pts@planes[i,:2]+planes[i,2]).tolist())
 if not zs:raise RuntimeError('No triangle coverage '+ident)
 mn,mx=min(zs),max(zs);foundation.append({'id':ident,'footprint_area_m2':p.area,'uncovered_area_m2':gap,'triangle_intersections':len(ids),'min_m':mn,'max_m':mx,'span_m':mx-mn});return mn,mx
selected=tasks['representative_site_ids'] if args.scope=='representative' else [s['id'] for s in tasks['sites']]
if not selected:raise ValueError('No selected sites')
sites=[s for s in tasks['sites'] if s['id'] in selected];buildings=[]
for raw in tasks['buildings']:
 if raw['court_id'] not in selected:continue
 b=dict(raw);p=toworld(shape(b['footprint_px1']));mn,mx=fit(p,b['id']);b.update(foundation_min_m=mn,foundation_max_m=mx,floor_m=mx+.09,max_plinth_m=mx+.09-mn);buildings.append(b)
 if mx-mn>.65:raise RuntimeError(f"{b['id']} span {mx-mn:.2f}m needs smaller or stepped body")
region=unary_union([shape(s['footprint_px1']) for s in sites]).buffer(.3/M);wall=toworld(shape(tasks['wall_network_px1']).intersection(region));extrusions=[];captests=[]
def extrude(p,ident,category,mat,height):
 if p.is_empty:return
 for k,q in enumerate(shapely.get_parts(p)):
  if q.geom_type!='Polygon' or q.area<.003:continue
  # Delaunay constrained by all outer/hole edges: no convex-fan shortcuts.
  tris=list(shapely.constrained_delaunay_triangles(q).geoms);coords=[];lookup={};top=[]
  def idx(pt):
   key=tuple(pt)
   if key not in lookup:lookup[key]=len(coords);coords.append(list(pt))
   return lookup[key]
  for t in tris:
   points=list(t.exterior.coords)[:-1];cross=(points[1][0]-points[0][0])*(points[2][1]-points[0][1])-(points[1][1]-points[0][1])*(points[2][0]-points[0][0]);points=points if cross>0 else list(reversed(points));top.append([idx(p) for p in points])
  rings=[[idx(pt) for pt in list(r.coords)[:-1]] for r in [q.exterior,*q.interiors]]
  union=unary_union(tris);delta=union.symmetric_difference(q).area
  captests.append({'id':ident+f'_{k}','target_area_m2':q.area,'excess_m2':union.difference(q).area,'missing_m2':q.difference(union).area,'symmetric_difference_m2':delta,'boundary_hausdorff_m':union.boundary.hausdorff_distance(q.boundary)})
  assert delta<1e-8
  mn,mx=fit(q,ident+f'_{k}')
  if category=='courtyard_paving' and mx-mn>.30:continue
  extrusions.append({'id':ident+f'_{k}','xy_m':coords,'top_triangles':top,'boundary_rings':rings,'z_bottom_m':mn-.04,'z_top_m':mx+height,'material':mat,'category':category,'foundation_span_m':mx-mn})
# Small shared-wall prisms follow source elevations. Grid subdivision is geometry scheduling only.
x0,y0,x1,y1=wall.bounds
for ix in range(math.floor(x0/2.5),math.ceil(x1/2.5)):
 for iy in range(math.floor(y0/2.5),math.ceil(y1/2.5)):
  p=wall.intersection(box(ix*2.5,iy*2.5,(ix+1)*2.5,(iy+1)*2.5));extrude(p,f'W_{ix}_{iy}','courtyard_wall','BrickWall',tasks['wall_height_m'])
gates=[]
for raw in tasks['gates']:
 if raw['court_id'] not in selected:continue
 g=dict(raw);x,y=g['center_local_m'];p=localpoly(g,[x-1.9,y-.15,x+1.9,y+1.5]);mn,mx=fit(p,g['id']);g.update(floor_m=mx+.04,bottom_m=mn-.04);gates.append(g)
for s in sites:
 main=next((b for b in buildings if b['court_id']==s['id'] and b['archetype']!='annex_shed'),None);g=next((g for g in gates if g['court_id']==s['id']),None)
 if not g or not main:continue
 q=main['bounds_local_m'];a=xy(main['origin_px1'],main['angle_image_deg'],(q[0]+q[2])/2,q[1]-.7);path=toworld(LineString([a,g['center_px1']]).buffer(.62/M,cap_style=2));path=path.intersection(toworld(shape(s['footprint_px1']))).difference(unary_union([toworld(shape(b['footprint_px1'])).buffer(.1) for b in buildings]));xx,yy,xxx,yyy=path.bounds
 for ix in range(math.floor(xx/1.8),math.ceil(xxx/1.8)):
  for iy in range(math.floor(yy/1.8),math.ceil(yyy/1.8)):
   extrude(path.intersection(box(ix*1.8,iy*1.8,(ix+1)*1.8,(iy+1)*1.8)),s['id']+f'_PATH_{ix}_{iy}','courtyard_paving','Courtyard',.022)
# Mandatory concave-with-hole template regression, independent of a site's shape.
q=Polygon([(0,0),(5,0),(5,1),(2,1),(2,4),(0,4)],holes=[[(.4,.4),(.4,.8),(1.2,.8),(1.2,.4)]])
u=unary_union(list(shapely.constrained_delaunay_triangles(q).geoms));captests.append({'id':'REGRESSION_CONCAVE_WITH_HOLE','symmetric_difference_m2':u.symmetric_difference(q).area,'excess_m2':u.difference(q).area,'missing_m2':q.difference(u).area,'boundary_hausdorff_m':u.boundary.hausdorff_distance(q.boundary)})
spec={'coordinate_space':tasks['coordinate_space'],'label_shape':tasks['label_shape'],'courtyards':sites,'buildings':buildings,'gates':gates,'extrusions':extrusions,'source_tasks_sha256':hashlib.sha256((O/'specs/tasks.json').read_bytes()).hexdigest(),'stage':3 if args.scope=='representative' else 4}
save(O/'specs'/f'{args.scope}_compiled.json',spec);save(O/'reports'/f'{args.scope}_foundation_report.json',{'method':'entire footprint clipped against actual original triangle projections; extrema from every intersection vertex','items':foundation});save(O/'reports'/f'{args.scope}_triangulation_report.json',{'status':'PASS','items':captests})
# Textures belong to the selected style, never to this executor.
style=json.loads((O/'specs/style.json').read_text(encoding='utf-8'))
T=O/'geometry/textures';T.mkdir(exist_ok=True)
for material in style['materials'].values():
 name=material.get('texture')
 if not name:continue
 source=style.get('texture_assets',{}).get(name)
 if not source:raise ValueError(f'Texture {name} has no texture_assets source')
 path=(R/source).resolve()
 if not path.is_file():raise FileNotFoundError(path)
 shutil.copy2(path,T/name)
print(json.dumps({'sites':selected,'buildings':len(buildings),'gates':len(gates),'extrusions':len(extrusions),'max_house_plinth_m':max(b['max_plinth_m'] for b in buildings),'wall_cap_tests':len(captests)},ensure_ascii=False))

