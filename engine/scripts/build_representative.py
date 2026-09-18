import bpy, bmesh, math, json, hashlib, traceback, shutil, time, sys
sys.dont_write_bytecode=True
from pathlib import Path
from mathutils import Vector
from mathutils.bvhtree import BVHTree
import importlib.util
JOB=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text(encoding='utf-8'))
R=Path(JOB['work_root']).resolve(); SCOPE=JOB.get('scope','representative'); O=R/'outputs/geometry'/SCOPE
PROJECT=Path(JOB['project_root']).resolve()
INPUTS={k:Path(v).resolve() for k,v in JOB['inputs'].items()}
STYLE=json.loads(Path(JOB['style_file']).read_text(encoding='utf-8'))
P=STYLE['geometry']
UV_REPEAT=float(P.get('uv_repeat_m',2.0))
O.mkdir(parents=True,exist_ok=True); (O/'textures').mkdir(exist_ok=True)
RECEIPT=R/'outputs/reports'/f'{SCOPE}_generation_receipt.json'
START=time.time()
def status(stage,**kw):
    (R/'outputs/reports'/f'{SCOPE}_generation_progress.json').write_text(json.dumps({'stage':stage,'elapsed_s':round(time.time()-START,1),**kw}),encoding='utf-8')
def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version=0
    scene=bpy.context.scene
    scene.unit_settings.system='METRIC'; scene.unit_settings.scale_length=.01
    spec=importlib.util.spec_from_file_location('map_common',INPUTS['map_common'])
    mc=importlib.util.module_from_spec(spec); spec.loader.exec_module(mc)
    layout=json.loads((R/'outputs/specs'/f'{SCOPE}_compiled.json').read_text())
    co,si,ox,oy=1.,0.,0.,0.
    def world(x,y,z): return (ox+100*(co*x-si*y),oy+100*(si*x+co*y),100*z)
    def collection(name):
        c=bpy.data.collections.new(name); scene.collection.children.link(c); return c
    basecol=collection('BaseTerrain'); detailcol=collection('Details'); contextcol=collection('Context')
    mats={}; material_info={}
    def material(key,color=None,texture=None,roughness=.75,metallic=0):
        m=bpy.data.materials.new('M_Building_'+key); m.use_nodes=True
        bs=m.node_tree.nodes.get('Principled BSDF'); bs.inputs['Roughness'].default_value=roughness; bs.inputs['Metallic'].default_value=metallic
        if color: bs.inputs['Base Color'].default_value=(*color,1); m.diffuse_color=(*color,1)
        if texture:
            dest=O/'textures'/Path(texture).name; shutil.copy2(texture,dest)
            t=m.node_tree.nodes.new('ShaderNodeTexImage'); t.image=bpy.data.images.load(str(dest)); t.extension='REPEAT'
            m.node_tree.links.new(t.outputs['Color'],bs.inputs['Base Color'])
            bump=m.node_tree.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value=.12; bump.inputs['Distance'].default_value=.6
            m.node_tree.links.new(t.outputs['Color'],bump.inputs['Height']); m.node_tree.links.new(bump.outputs['Normal'],bs.inputs['Normal'])
        mats[key]=m; material_info[key]={'color':color or [.6,.6,.6],'texture':('textures/'+Path(texture).name) if texture else None,'roughness':roughness,'metallic':metallic}
        return m
    for k,v in STYLE['materials'].items():
        material(k,color=v.get('color'),texture=str(R/'outputs/geometry/textures'/v['texture']) if v.get('texture') else None,roughness=v['roughness'],metallic=v.get('metallic',0))
    def load_obj(path,col):
        verts=[]; faces=[]; uvs=[]; uvfaces=[]
        with Path(path).open(encoding='utf-8') as f:
            for line in f:
                if line.startswith('v '): verts.append(tuple(map(float,line.split()[1:4])))
                elif line.startswith('vt '): uvs.append(tuple(map(float,line.split()[1:3])))
                elif line.startswith('f '):
                    pieces=[p.split('/') for p in line.split()[1:]]
                    faces.append([int(p[0])-1 for p in pieces]); uvfaces.append([int(p[1])-1 if len(p)>1 and p[1] else -1 for p in pieces])
        me=bpy.data.meshes.new(Path(path).stem); me.from_pydata(verts,[],faces); me.update()
        if uvs:
            layer=me.uv_layers.new(name='UVMap')
            for p,ids in zip(me.polygons,uvfaces):
                for li,ui in zip(p.loop_indices,ids):
                    if ui>=0: layer.data[li].uv=uvs[ui]
        ob=bpy.data.objects.new(Path(path).stem,me); col.objects.link(ob)
        ob.hide_select=True; ob.lock_location=(True,)*3; ob.lock_rotation=(True,)*3; ob.lock_scale=(True,)*3
        return ob,verts,faces
    status('importing_original_base')
    source=INPUTS['base_obj']
    base,verts,faces=load_obj(source,basecol)
    bm=bpy.data.materials.new('M_Building'); bm.use_nodes=True; bs=bm.node_tree.nodes.get('Principled BSDF'); bs.inputs['Base Color'].default_value=(.82,.78,.7,1); bs.inputs['Roughness'].default_value=.92; base.data.materials.append(bm)
    tree=BVHTree.FromPolygons([Vector(v) for v in verts],faces,all_triangles=True)
    def height(x,y):
        wx,wy,_=world(x,y,0); hit=tree.ray_cast(Vector((wx,wy,100000)),Vector((0,0,-1)),200000)[0]
        if hit is None: raise RuntimeError(f'No original foundation under {x},{y}')
        return hit.z/100
    ground,_,_=load_obj(INPUTS['ground_obj'],contextcol)
    gm=bpy.data.materials.new('Context_Ground'); gm.diffuse_color=(.21,.245,.145,1); gm.use_nodes=True; gm.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=(.21,.245,.145,1); ground.data.materials.append(gm)
    objects=[]; foundation=[]
    def mesh(name,vs,fs,mat,category):
        me=bpy.data.meshes.new(name); me.from_pydata([world(*v) for v in vs],[],fs); me.update()
        b=bmesh.new(); b.from_mesh(me); bmesh.ops.recalc_face_normals(b,faces=b.faces); b.to_mesh(me); b.free(); me.update()
        uv=me.uv_layers.new(name='UVMap')
        for p in me.polygons:
            nx=co*p.normal.x+si*p.normal.y; ny=-si*p.normal.x+co*p.normal.y; nz=p.normal.z
            for li in p.loop_indices:
                x,y,z=vs[me.loops[li].vertex_index]
                uv.data[li].uv=(x/UV_REPEAT,y/UV_REPEAT) if abs(nz)>.65 else ((x/UV_REPEAT,z/UV_REPEAT) if abs(ny)>=abs(nx) else (y/UV_REPEAT,z/UV_REPEAT))
        ob=bpy.data.objects.new(name,me); detailcol.objects.link(ob); me.materials.append(mats[mat]); ob['category']=category; objects.append(ob); return ob
    def box(name,x0,x1,y0,y1,z0,z1,mat,category='detail'):
        vs=[(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
        return mesh(name,vs,[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],mat,category)
    def prism_x(name,x0,x1,yz,mat,category):
        n=len(yz); vs=[(x,y,z) for x in [x0,x1] for y,z in yz]; fs=[tuple(range(n-1,-1,-1)),tuple(range(n,2*n))]
        fs += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
        return mesh(name,vs,fs,mat,category)
    def roof(name,x0,x1,y0,y1,basez,mat,height=3.35,pitch=25):
        ym=(y0+y1)/2; slope=math.tan(math.radians(pitch)); ridge=basez+height+(y1-y0)/2*slope
        e=P['roof_eave_m']; t=P['roof_thickness_m']; x=P['roof_end_overhang_m']; ridge_x=P['ridge_end_overhang_m']
        eavez=basez+height-e*slope
        for j,section in enumerate([[(y0-e,eavez-t),(ym,ridge-t),(ym,ridge),(y0-e,eavez)],[(ym,ridge-t),(y1+e,eavez-t),(y1+e,eavez),(ym,ridge)]]):
            prism_x(name+f'_slope{j}',x0-x,x1+x,section,mat,'roof')
        prism_x(name+'_ridge',x0-ridge_x,x1+ridge_x,[(ym-.12,ridge-.015),(ym+.12,ridge-.015),(ym+.09,ridge+.1),(ym,ridge+.15),(ym-.09,ridge+.1)],mat,'roof_trim')
        for y in [y0-.33,y1+.23]: box(name+'_fascia'+str(y),x0-.28,x1+.28,y,y+.1,eavez-.17,eavez+.01,'Door','roof_trim')
        return ridge
    def opening(name,cx,y,basez,width=1.9,heightw=1.5):
        z=basez+P['window_sill_height_m']; left=cx-width/2; right=cx+width/2
        box(name+'_glass',left,right,y-.07,y-.018,z,z+heightw,'WindowGlass','window')
        for k,x in enumerate([left,left+width/3,left+2*width/3,right-.06]): box(name+f'_v{k}',x,x+.06,y-.12,y-.065,z-.04,z+heightw+.04,'WindowFrame','window_frame')
        for k,zz in enumerate([z-.04,z+heightw*.72,z+heightw]): box(name+f'_h{k}',left-.04,right+.04,y-.12,y-.065,zz,zz+.065,'WindowFrame','window_frame')
        box(name+'_sill',left-.13,right+.13,y-.25,y+.015,z-.13,z-.045,'LightTrim','window_sill')
        box(name+'_lintel',left-.11,right+.11,y-.09,y+.01,z+heightw+.06,z+heightw+.19,'LightTrim','window_lintel')
    def door(name,cx,y,bz):
        box(name+'_backing',cx-.71,cx+.71,y-.025,y-.005,bz,bz+2.26,'Door','door_backing')
        for k,x in enumerate([cx-.7,cx+.02]):
            box(name+f'_leaf{k}',x,x+.68,y-.075,y-.018,bz+.05,bz+2.22,'Door','door')
            for j,z in enumerate([bz+.27,bz+1.03]): box(name+f'_panel{k}_{j}',x+.1,x+.58,y-.091,y-.075,z,z+.65,'DoorPanel','door_detail')
            box(name+f'_handle{k}',cx+(-.13 if k==0 else .1),cx+(-.1 if k==0 else .13),y-.14,y-.095,bz+1.02,bz+1.21,'Metal','door_detail')
        for k,x in enumerate([cx-.8,cx+.71]): box(name+f'_jamb{k}',x,x+.09,y-.125,y+.01,bz,bz+2.35,'Door','door_frame')
        box(name+'_lintel',cx-.82,cx+.82,y-.135,y+.01,bz+2.25,bz+2.39,'Door','door_frame')
        box(name+'_step',cx-.96,cx+.96,y-.6,y+.02,min(height(cx-.9,y-.5),height(cx+.9,y-.5),bz-.05)-.03,bz+.06,'Stone','threshold')

    def set_frame(c=None):
        nonlocal co,si,ox,oy
        if c is None:co,si,ox,oy=1.,0.,0.,0.;return
        a=math.radians(-c['angle_image_deg']);co,si=math.cos(a),math.sin(a)
        ox=mc.ue_x_of_col(c['origin_px1'][0]*mc.SCALE,layout['label_shape'][1]);oy=mc.ue_y_obj_of_row(c['origin_px1'][1]*mc.SCALE,layout['label_shape'][0])
    # Retain the source Ground material assignments for alignment context.
    ground.data.materials.clear();groundcolors={};current=None
    for line in INPUTS['ground_mtl'].read_text().splitlines():
        if line.startswith('newmtl '):current=line.split()[1]
        elif line.startswith('Kd ') and current:groundcolors[current]=tuple(map(float,line.split()[1:4]))
    slots={}
    for key,color in groundcolors.items():
        ma=bpy.data.materials.new('Context_'+key);ma.use_nodes=True;ma.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=(*color,1);ma.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=.9
        slots[key]=len(ground.data.materials);ground.data.materials.append(ma)
    faceid=0;matid=0
    with INPUTS['ground_obj'].open(encoding='utf-8') as stream:
        for line in stream:
            if line.startswith('usemtl '):matid=slots.get(line.split()[1],0)
            elif line.startswith('f '):ground.data.polygons[faceid].material_index=matid;faceid+=1
    status('building_all_houses',count=len(layout['buildings']))
    for i,b in enumerate(layout['buildings']):
        if b['archetype'] not in {'rural_house','annex_shed','factory_hall'}:
            raise ValueError('Unsupported archetype '+str(b['archetype'])+'; extend and review the executor before generation')
        set_frame(b);ident=b['id'];x0,y0,x1,y1=b['bounds_local_m'];bz=b['floor_m'];h=b['height_m'];wid=x1-x0
        foundation.append({'id':ident,'source_min_m':b['foundation_min_m'],'source_max_m':b['foundation_max_m'],'floor_m':bz,'plinth_m':b['max_plinth_m']})
        box(ident+'_plinth',x0,x1,y0,y1,b['foundation_min_m']-P['plinth_below_foundation_m'],bz+.004,'Stone','foundation')
        if b['archetype']=='annex_shed':
            box(ident+'_body',x0,x1,y0,y1,bz-.01,bz+P['annex_wall_height_m'],b['wall_material'],'annex_wall')
            prism_x(ident+'_upper',x0,x1,[(y0,bz+2.58),(y1,bz+2.58),(y1,bz+3.0)],b['wall_material'],'annex_upper_wall')
            prism_x(ident+'_roof',x0-.2,x1+.2,[(y0-.23,bz+2.56),(y1+.23,bz+3.0),(y1+.23,bz+3.12),(y0-.23,bz+2.68)],b['roof_material'],'annex_roof')
            door(ident+'_door',(x0+x1)/2,y0,bz)
        else:
            pitch=b['roof_pitch_deg'];ridge=bz+h+(y1-y0)/2*math.tan(math.radians(pitch))
            prism_x(ident+'_body',x0,x1,[(y0,bz-.01),(y1,bz-.01),(y1,bz+h-.10),((y0+y1)/2,ridge-.10),(y0,bz+h-.10)],b['wall_material'],'main_wall' if b['archetype']=='rural_house' else 'factory_wall')
            roof(ident+'_roof',x0,x1,y0,y1,bz,b['roof_material'],height=h,pitch=pitch)
            box(ident+'_skirting',x0-.015,x1+.015,y0-.035,y0-.002,bz,bz+.32,'Stone','wall_trim')
            if b['archetype']=='factory_hall':
                cx=(x0+x1)/2;box(ident+'_loadingdoor',cx-1.55,cx+1.55,y0-.065,y0-.015,bz+.04,bz+3.6,'Metal','factory_door')
                for k in range(13):box(ident+f'_doorslat{k}',cx-1.5+k*.24,cx-1.46+k*.24,y0-.09,y0-.063,bz+.06,bz+3.55,'LightTrim','door_detail')
                for k,wx in enumerate([x0+wid*.18,x0+wid*.32,x0+wid*.68,x0+wid*.82]):
                    if abs(wx-cx)>2.8:opening(ident+f'_window{k}',wx,y0,bz+.7,width=1.7,heightw=1.3)
            else:
                door(ident+'_door',(x0+x1)/2,y0,bz)
                ww=min(P['window_max_width_m'],wid*P['window_width_ratio'])
                opening(ident+'_window_L',x0+wid*.22,y0,bz,width=ww)
                opening(ident+'_window_R',x0+wid*.78,y0,bz,width=ww)
        if i%25==0:status('building_all_houses',done=i,total=len(layout['buildings']),objects=len(objects))
    set_frame()
    status('building_shared_walls_and_surfaces',count=len(layout['extrusions']))
    for i,s in enumerate(layout['extrusions']):
        xy=s['xy_m'];n=len(xy);z0=s['z_bottom_m'];z1=s['z_top_m'];vs=[(x,y,z) for z in [z0,z1] for x,y in xy]
        fs=[tuple(reversed(t)) for t in s['top_triangles']]+[tuple(j+n for j in t) for t in s['top_triangles']]
        for ring in s['boundary_rings']:
            fs += [(a,b,b+n,a+n) for a,b in zip(ring,ring[1:]+ring[:1])]
        mesh(s['id'],vs,fs,s['material'],s['category'])
        if s['category']=='courtyard_wall':
            cv=[(x,y,z) for z in [z1,z1+.08] for x,y in xy];mesh(s['id']+'_COPING',cv,fs,'Stone','wall_coping')
        if i%200==0:status('building_shared_walls_and_surfaces',done=i,total=len(layout['extrusions']),objects=len(objects))
    status('building_gate_frames')
    for g in layout['gates']:
        set_frame(g);ident=g['id'];pt=Vector(g['center_local_m']);e=Vector(g['edge_unit_local']);inside=Vector(g['inward_unit_local']);bz=g['floor_m'];half=g['width_m']/2
        def gatebox(name,t0,t1,u0,u1,z0,z1,mat,category):
            ps=[pt+e*t+inside*u for t,u in [(t0,u0),(t1,u0),(t1,u1),(t0,u1)]]
            return mesh(name,[(p.x,p.y,z) for z in [z0,z1] for p in ps],[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],mat,category)
        for k,t in enumerate([-half-.2,half+.2]):
            gatebox(ident+f'_POST{k}',t-.2,t+.2,-.1,.3,g['bottom_m'],bz+2.35,'BrickWall','gate_pier')
            gatebox(ident+f'_CAP{k}',t-.22,t+.22,-.12,.32,bz+2.35,bz+2.43,'Stone','gate_trim')
        gatebox(ident+'_BEAM',-half-.4,half+.4,-.09,.29,bz+2.18,bz+2.37,'Door','gate_lintel')
        gatebox(ident+'_THRESHOLD',-half,half,-.1,.6,g['bottom_m'],bz+.012,'Stone','gate_threshold')
        for side,sgn in [('L',1),('R',-1)]:
            hinge=pt-e*sgn*half;du=e*sgn*math.cos(math.radians(68))+inside*math.sin(math.radians(68));normal=Vector((-du.y,du.x))*.055;leaf=half-.07
            ps=[hinge,hinge+du*leaf,hinge+du*leaf+normal,hinge+normal]
            mesh(ident+'_LEAF_'+side,[(p.x,p.y,z) for z in [bz+.08,bz+2.07] for p in ps],[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],'Door','gate_leaf')
    set_frame()
    # Validate closedness and eliminate invalid mesh construction before rendering.
    issues=[]; ntri=0; per_object=[]
    for ob in objects:
        b=bmesh.new(); b.from_mesh(ob.data)
        non=sum(not e.is_manifold for e in b.edges); zero=sum(f.calc_area()<1e-6 for f in b.faces); vol=b.calc_volume(signed=True)
        if non or zero or vol<=0: issues.append({'id':ob.name,'nonmanifold_edges':non,'zero_area_faces':zero,'volume_cm3':vol})
        bmesh.ops.triangulate(b,faces=b.faces); b.to_mesh(ob.data); b.free(); ob.data.update()
        ntri+=len(ob.data.polygons); per_object.append({'id':ob.name,'category':ob['category'],'triangles':len(ob.data.polygons),'material':ob.data.materials[0].name})
    (R/'outputs/reports'/f'{SCOPE}_mesh_precheck.json').write_text(json.dumps({'issues':issues,'triangles':ntri,'objects':per_object,'foundation':foundation},indent=2))
    if issues: raise RuntimeError('Mesh precheck failed: '+json.dumps(issues[:8]))
    status('exporting_fullmap',triangles=ntri,objects=len(objects))
    # Preserve original OBJ bytes verbatim, then append newly generated objects.
    output=O/f'{SCOPE}_details.obj'; original=source.read_bytes(); output.write_bytes(f'mtllib {SCOPE}_details.mtl\n'.encode())
    vc=0; tc=0; nc=0
    with output.open('a',encoding='utf-8',newline='\n') as f:
        for ob in objects:
            me=ob.data; f.write('\no '+ob.name+'\n')
            for v in me.vertices: f.write('v %.6f %.6f %.6f\n'%tuple(v.co))
            for loop in me.loops:
                uv=me.uv_layers.active.data[loop.index].uv; f.write('vt %.7f %.7f\n'%tuple(uv))
            for p in me.polygons:
                for li in p.loop_indices: f.write('vn %.8f %.8f %.8f\n'%tuple(p.normal))
            f.write('usemtl '+me.materials[0].name+'\n')
            for p in me.polygons: f.write('f '+' '.join(f'{vc+me.loops[li].vertex_index+1}/{tc+li+1}/{nc+li+1}' for li in p.loop_indices)+'\n')
            vc+=len(me.vertices); tc+=len(me.loops); nc+=len(me.loops)
    mtl=''
    for k,v in material_info.items():
        mtl+='\nnewmtl M_Building_'+k+'\nKd '+' '.join(map(str,[1,1,1] if v['texture'] else v['color']))+'\nKs 0.12 0.12 0.12\nNs 32\n'
        if v['texture']: mtl+='map_Kd '+v['texture']+'\n'
    (O/f'{SCOPE}_details.mtl').write_text(mtl,encoding='utf-8')
    objhash=hashlib.sha256(output.read_bytes()).hexdigest()

    status('saving_native_scene')
    scene.render.engine='CYCLES';scene.cycles.samples=36;scene.cycles.use_denoising=True
    scene.world=bpy.data.worlds.new('NeutralWorld');scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.72,.79,.88,1);scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.65
    scene.view_settings.view_transform='AgX';scene.render.image_settings.file_format='PNG';scene.render.film_transparent=False
    sun_data=bpy.data.lights.new('Sun','SUN');sun_data.energy=2.3;sun_data.angle=math.radians(16);sun=bpy.data.objects.new('Sun',sun_data);scene.collection.objects.link(sun);sun.rotation_euler=(math.radians(25),math.radians(-20),math.radians(-25))
    for im in bpy.data.images:
        if im.source=='FILE':im.pack()
    metadata=[]
    for ob in objects:
        xyz=[v.co for v in ob.data.vertices];metadata.append({'id':ob.name,'category':ob['category'],'aabb_min_cm':[min(v[i] for v in xyz) for i in range(3)],'aabb_max_cm':[max(v[i] for v in xyz) for i in range(3)],'triangles':len(ob.data.polygons),'material':ob.data.materials[0].name})
    (R/'outputs/reports'/f'{SCOPE}_object_manifest.json').write_text(json.dumps({'model_sha256':objhash,'objects':metadata},separators=(',',':')))
    bpy.ops.wm.save_as_mainfile(filepath=str(O/f'{SCOPE}.blend'),compress=True)
    report={'status':'GEOMETRY_COMPLETE_PENDING_VALIDATION','model_sha256':objhash,'generation_spec_sha256':hashlib.sha256((R/'outputs/specs'/f'{SCOPE}_compiled.json').read_bytes()).hexdigest(),'style_sha256':hashlib.sha256(Path(JOB['style_file']).read_bytes()).hexdigest(),'source_sha256':hashlib.sha256(original).hexdigest(),'original_source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==hashlib.sha256(original).hexdigest(),'base_faces':len(faces),'detail_triangles':ntri,'detail_objects':len(objects),'buildings':len(layout['buildings']),'courtyards':len(layout['courtyards']),'materials':len(mats)+1,'textures':sum(bool(v.get('texture')) for v in STYLE['materials'].values()),'texture_decode_mib':sum(im.size[0]*im.size[1]*4 for im in bpy.data.images if im.source=='FILE')/1048576,'foundation':foundation,'elapsed_s':time.time()-START,'candidate':str(O)}
    RECEIPT.write_text(json.dumps(report,indent=2));status('geometry_complete',triangles=ntri,objects=len(objects))
try:
    main()
except Exception:
    err=traceback.format_exc();RECEIPT.write_text(json.dumps({'status':'FAILED','error':err,'elapsed_s':time.time()-START},indent=2));status('failed');print(err)
