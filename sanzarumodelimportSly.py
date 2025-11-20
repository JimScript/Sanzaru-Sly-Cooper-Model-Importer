bl_info = {
    "name": "Sly Cooper/Sanzaru Model Importer",
    "description": "Model importer for the 3DS Sonic Boom games and other Sanzaru games",
    "author": "AdelQ - edited by: JimScript",
    "version": (0, 9),
    "blender": (3, 6, 5),
    "location": "File > Import",
    "warning": "Texture imports currently unsupported",
    "category": "Import-Export",
}

import bpy
import bmesh
import struct
import mathutils
import os
import math
from io import BytesIO 
from re import findall
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty, CollectionProperty
from bpy.types import Operator

# Call this function and concatenate the data types into one string in the struct.unpack functions
def endian_sel(bool: big = False):
	if big == True:
		return ">"
	else:
		return "<"

# Turn this into a UI checkbox or detection function or similar, and return a bool for if it's big endian
def function_for_detecting_big_endianness():
	return True

# Call it where needed
is_big_endian = function_for_detecting_big_endianness()

class SanzaruGEOB:
    def __init__(self, file):
        self.name = ""
        self.bone_count = 0
        self.bone_transforms = []
        self.bone_names = []
        self.bone_parents = []
        self.hash = 0        

        # GEOB - GEOB Identifier 
        magic = file.read(4)
        if magic != b"GEOB":
            invalid_format("GEOB", file.tell(), magic) 
        geob_length = struct.unpack("<I", file.read(4))[0]

        # GEOH - Geo Header           
        magic = file.read(4)
        offset = file.tell()
        if magic != b"GEOH":
            invalid_format("GEOH", file.tell(), magic)
        geoh_length = struct.unpack("<I", file.read(4))[0]
        geoh_version = struct.unpack("<B", file.read(1))[0]
        file.read(0x18) # Bounding box min/max
        name_hash = struct.unpack("<i",file.read(4))[0]
        anim_hash = struct.unpack("<i",file.read(4))[0]
        file.read(4) # Light group
        self.name = file.read(0x2B).split(b'\x00')[0].decode() # Max string length found is 0x19, made longer just in case. May break with versions <6
        file.read(geoh_length - file.tell() + offset) # Adaptive read in case of longer headers
        
        magic = file.read(4)
        
        # SKEL - Skeleton Header
        if magic == b"SKEL":
            skel_length = struct.unpack("<I", file.read(4))[0]
            
            # SKHD - Skeleton Header
            magic = file.read(4)
            offset = file.tell()
            if magic != b"SKHD":
                invalid_format("SKHD", file.tell(), magic)
            skhd_length = struct.unpack("<I", file.read(4))[0]
            skhd_version = struct.unpack("<B", file.read(1))[0]
            self.bone_count = struct.unpack("<h", file.read(2))[0]
            file.read(skhd_length - file.tell() + offset) # Adaptive read in case of longer headers

            # BONS - Bones chunk
            magic = file.read(4)
            if magic != b"BONS":
                invalid_format("BONS", file.tell(), magic)
            bons_length = struct.unpack("<I", file.read(4))[0]
            
            bone_hashes = {}
            bone_parent_hashes = []
            
            for _ in range(self.bone_count):
                bone_name_hash, parent_name_hash = struct.unpack("<ii",file.read(8))
                x_basis = mathutils.Vector(struct.unpack("<fff",file.read(0xC)))
                z_basis = mathutils.Vector(struct.unpack("<fff",file.read(0xC)))
                position = mathutils.Vector(struct.unpack("<fff",file.read(0xC)))
                bone_name = file.read(0x20).split(b'\x00')[0].decode()

                bone_name_hash = str(bone_name_hash)
                parent_name_hash = str(parent_name_hash)

                y_basis = z_basis.cross(x_basis)
                quat = mathutils.Matrix((z_basis,x_basis,y_basis)).transposed().to_quaternion()
                transforms = (position, quat)
                
                self.bone_names.append(bone_name)
                self.bone_transforms.append(transforms)
                bone_hashes.update({bone_name_hash:bone_name})
                bone_parent_hashes.append(parent_name_hash)
            
            # Match hashes with indices
            for i in range(self.bone_count):
                if bone_parent_hashes[i] == "0":
                    parent_index = "@none"
                else:
                    parent_index = bone_hashes[bone_parent_hashes[i]]
                self.bone_parents.append(parent_index)
            
            magic = file.read(4)
            
        # GLOD - GLOD Identifier 
        if magic != b"GLOD":
            print(magic)
            invalid_format("GLOD or SKEL", file.tell(), magic)

        glod_length = struct.unpack("<I", file.read(4))[0]
        file.read(1) # Always 0, possibly version number
        self.hash = struct.unpack("<i",file.read(4))[0]
        switch_distance = struct.unpack("<f",file.read(4))[0]
        
        file.close()
        del file
        
    def make_skel(self):
        
        if bpy.context.active_object:
            bpy.ops.object.mode_set(mode='OBJECT')
            
        bpy.ops.object.add(type='ARMATURE',enter_editmode=1)
        obj = bpy.context.active_object
        
        name = self.name + "_skeleton"
        obj.name = name
        obj.data.name = name
        
        # Create bones
        for i in range(self.bone_count):
            edit_bone = obj.data.edit_bones.new(self.bone_names[i])
            edit_bone.use_connect = False
            edit_bone.use_local_location = False
            edit_bone.head = self.bone_transforms[i][0]
            edit_bone.tail = edit_bone.head + mathutils.Vector((0,0.1,0))

        bpy.ops.object.mode_set(mode='POSE')

        for i, pose_bone in enumerate(obj.pose.bones): # Apply Rotations | TODO: Orient bones without pose mode
            pose_bone.rotation_mode = 'QUATERNION'
            pose_bone.rotation_quaternion = self.bone_transforms[i][1]
        bpy.ops.pose.armature_apply()

        bpy.ops.object.mode_set(mode='EDIT')


        for i, bone in enumerate(obj.data.edit_bones): # Set parents
            if self.bone_parents[i] != "@none":
                parent_bone = self.bone_parents[i]
                bone.parent = obj.data.edit_bones[parent_bone]
            bone.use_local_location = True
        
        # Calculate bone lengths
        for bone in obj.data.edit_bones:
            test_lengths = [0.05] # Min Length
            if bone.children:
                for child_bone in bone.children:
                    temp_length = (bone.head - child_bone.head).length
                    if 1.0 > temp_length > 0.05: # Arbitrary length limit
                        test_lengths.append(temp_length)
            bone.length = max(test_lengths)
        
        # Debug only
        #for i in range(len(obj.data.edit_bones)):
            #bone = obj.data.edit_bones[i]
            #print(f"{i} {bone.name}")
            
        bpy.ops.object.mode_set(mode='OBJECT')
        
        self.pose_bones = obj.pose.bones
        
        return obj

class SanzaruSubmesh:    
    class Vertex:
        def __init__(self):
            self.count = 0
            self.coord = []
            self.uv = []
            self.nrm = []
            self.idx = []
            self.color = []
            self.weight = []
        
    class Face:
        def __init__(self):
            self.count = 0
            self.idx = []
            
    class Weight:
        def __init__(self):
            self.pal = []

    def __init__(self, file, geo):
        self.vertex = self.Vertex()
        self.face = self.Face()
        self.weight = self.Weight()
        self.length = 0
        self.vertex_scale = 1.0
        self.material_hash = 0
        self.get_weights = False
        if geo.bone_count:
            self.get_weights = True
        
        # SMSH - Submesh Identifier 
        magic = file.read(4)
        if magic != b"SMSH":
            invalid_format("SMSH", file.tell(), magic)
        smsh_length = struct.unpack("<I", file.read(4))[0]
        self.length = smsh_length
        
        # MHDR - Model Header    
        magic = file.read(4)
        if magic != b"MHDR":
            invalid_format("MHDR", file.tell(), magic)
        offset = file.tell()
        
        mhdr_length = struct.unpack("<I", file.read(4))[0]
        mhdr_version = struct.unpack("<B", file.read(1))[0]
        self.vertex.count = struct.unpack("<h", file.read(2))[0]
        idx_count = struct.unpack("<h", file.read(2))[0]
        self.face.count = int(idx_count / 3)
        file.read(1) # primitive type
        self.material_hash = struct.unpack("<i", file.read(4))[0]
        file.read(4) # vertex_def_hash
        if mhdr_version:
            self.vertex_scale = struct.unpack("<f", file.read(4))[0]
            if mhdr_version >= 2:
                 file.read(1) # vis_group
                # GOTO: LABEL_6:
        else:
            self.vertex_scale = 1.0
        
        # LABEL_6:
        if mhdr_version >= 3:
            file.read(8) # vertex_pack_buf offset and size
        if mhdr_version >= 4:
            file.read(0xC) # bound sphere
            file.read(8) # idx_pack_buf offset and size
            file.read(4) # stream1_pack_buf_size
            
        if mhdr_version >= 5:
            name_hash = struct.unpack("<i", file.read(4))[0]

        file.read(0xD)
        spare_bytes = struct.unpack("<B", file.read(1))[0]
        file.read(mhdr_length - file.tell() + offset) # WIP Vertex Definition Header, unsure beyond 14 bytes on MVTX


        # MVTX - Vertex Data 
        magic = file.read(4)
        if magic != b"MVTX":
            invalid_format("MVTX", file.tell(), magic)
        mvtx_length = struct.unpack("<I", file.read(4))[0]
        
        for _ in range(self.vertex.count):				
            vertex_pos = mathutils.Vector(struct.unpack(endian_sel(big=is_big_endian) + "eee", file.read(6))) #concatenate the strings for the first argument, should result in either "<eee" or ">eee" 
            #vertex_pos *= self.vertex_scale # Scale applied to mesh to apply non-destructively
            vertex_color = struct.unpack("<BBBB", file.read(4)) 
            uv_pos = struct.unpack(endian_sel(big=is_big_endian) + "ee", file.read(4)) #reverse endian for Vita
            uv_pos = (uv_pos[0], -uv_pos[1] + 1) # Invert UVs
            vertex_nrm = mathutils.Vector((0.0,0.0,0.0))#struct.unpack(">bbb", file.read(3)))
            if spare_bytes >=18:
                file.read(4)
            if spare_bytes >=22:
                file.read(4)
            if spare_bytes >=26:
                file.read(4)
            if spare_bytes >=30:
                file.read(4)
            if self.get_weights:
                vertex_nrm = mathutils.Vector(struct.unpack("<bbb", file.read(3)))
                vertex_index_raw = struct.unpack("<bbbb", file.read(4))
                vertex_weight_raw = struct.unpack("<BBBB", file.read(4))
                self.vertex.idx.append(list(vertex_index_raw))
                self.vertex.weight.append(vertex_weight_raw)
            
            self.vertex.coord.append(vertex_pos)
            self.vertex.color.append(vertex_color)
            self.vertex.uv.append(uv_pos)
            self.vertex.nrm.append(vertex_nrm)

        file.read(((mvtx_length - 4) % (spare_bytes)) % 4)
		# MIDX - Face Index
        magic = file.read(4)
        if magic != b'MIDX':
            invalid_format("MIDX", file.tell(), magic)
        midx_length = struct.unpack("<I", file.read(4))[0]
        
        for _ in range(self.face.count):
            face = struct.unpack(endian_sel(big=is_big_endian) + "HHH", file.read(6)) #reverse endian for Vita
            self.face.idx.append(face)
        
        if self.face.count % 2:
            file.read(2) # Byte alignment

        if self.get_weights:
            # MPAL - Weight pallete
            magic = file.read(4)
            if magic != b'MPAL':
                invalid_format("MPAL", file.tell(), magic)
            mpal_length = struct.unpack("<I", file.read(4))[0]
            weight_count = int((mpal_length - 4) / 2)
            for _ in range(weight_count):
                bone_index = struct.unpack("<h", file.read(2))[0]
                self.weight.pal.append(bone_index)

    def make_mesh(self, geo, index):
        if geo.bone_count:
            bones = geo.pose_bones
        index = str(index).zfill(2)
        
        name = f"{geo.name}_submesh{index}"
        
        bm = bmesh.new()
        me = bpy.data.meshes.new(name)

        for vertex in self.vertex.coord:
            bm.verts.new(vertex)
        bm.verts.ensure_lookup_table()

        for face in self.face.idx: #Some faces show up twice for some reason in the file, blender doesn't like this
            try:
                bm.faces.new([bm.verts[i] for i in face])
            except:
                continue
        bm.faces.ensure_lookup_table()
        
        bm.to_mesh(me) # Needed before applying UVs
        uv_layer = bm.loops.layers.uv.new("UVMap")
        uvs = self.vertex.uv
        for face in bm.faces:
            for loop in face.loops:
                loop[uv_layer].uv = uvs[loop.vert.index]
        
        vertex_colors_sub = []
        for color in self.vertex.color:
            vertex_colors_sub.append((color[0]/255, color[1]/255, color[2]/255, color[3]/255))
        
        
        color_layer = bm.loops.layers.color.new("Color")
        for f in bm.faces:
            for l in f.loops:
                l[color_layer]= vertex_colors_sub[l.vert.index]    
        
        bm.to_mesh(me)
        bm.free()

        # Add the mesh to the scene
        obj = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(obj)

        # Select and make active
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        if self.get_weights:
            group_names = []
            for i in self.weight.pal:
                group_names.append(bones[i].name)
            
            vertex_indices = []
            vertex_weights = []
            
            for i, vertex in enumerate(me.vertices):
                vertex_indices_sub = []
                vertex_weights_sub = []
                
                for j, weight in enumerate(self.vertex.weight[i]):
                    if weight > 0:
                        temp_index = self.vertex.idx[i][j]
                        vertex_indices_sub.append(temp_index)
                        temp_weight = self.vertex.weight[i][j]
                        temp_weight /= 255
                        vertex_weights_sub.append(temp_weight)
                vertex_indices.append(vertex_indices_sub)
                vertex_weights.append(vertex_weights_sub)
                
            vertex_group_refs = []
            for group_name in group_names:
                temp_group = obj.vertex_groups.new(name=group_name)
                vertex_group_refs.append(temp_group)

            for vertex_i, temp_weights in enumerate(vertex_weights):
                for group_i, temp_weight in enumerate(temp_weights):
                    ref_i = vertex_indices[vertex_i][group_i]
                    vertex_group_refs[ref_i].add([vertex_i], temp_weight, 'REPLACE')
        
        me.use_auto_smooth = True
        
        vertex_normals = self.vertex.nrm
        loop_normals = []
        
        for polygon in me.polygons:
            for loop_i in polygon.loop_indices:
                loop = me.loops[loop_i]
                vertex_i = loop.vertex_index
                loop_normals.append(vertex_normals[vertex_i])
        me.normals_split_custom_set(loop_normals)
        me.update()

        return obj
    
class SanzaruMaterial:
    def __init__(self, file):
        self.material_name = ""
        self.texture_name = ""
        self.material_hash = 0
        self.texture_hash = 0

        # MATL - MATL Identifier 
        magic = file.read(4)
        if magic != b"MATL":
            invalid_format("MATL", file.tell(), magic) 
        matl_length = struct.unpack("<I", file.read(4))[0]

        # MTLH - Material Header           
        magic = file.read(4)
        offset = file.tell()
        if magic != b"MTLH":
            invalid_format("MTLH", file.tell(), magic)
        mtlh_length = struct.unpack("<I", file.read(4))[0]
        mtlh_version = struct.unpack("<B", file.read(1))[0]
        self.texture_color_hash = struct.unpack("<i", file.read(4))[0]
        self.texture_fur_hash = struct.unpack("<i", file.read(4))[0]
        self.texture_normal_hash = struct.unpack("<i", file.read(4))[0]
        self.texture_specular_hash = struct.unpack("<i", file.read(4))[0]
        self.texture_lightwarp_hash = struct.unpack("<i", file.read(4))[0]
        self.material_hash = struct.unpack("<i", file.read(4))[0]
        self.material_name = file.read(0x20).split(b'\x00')[0].decode()
        # Material parameters (dif and spec color, uv clamp modes, etc)
    
    def parse_tex(self, file):
        # TEXR - TEXR Identifier 
        magic = file.read(4)
        if magic != b"TEXR":
            invalid_format("TEXR", file.tell(), magic) 
        texr_length = struct.unpack("<I", file.read(4))[0]

        # TXRH - Material Header           
        magic = file.read(4)
        offset = file.tell()
        if magic != b"TXRH":
            invalid_format("TXRH", file.tell(), magic)
        offset = file.tell()
        txrh_length = struct.unpack("<I", file.read(4))[0]
        txrh_version = struct.unpack("<B", file.read(1))[0]
        tex_hash = struct.unpack("<i", file.read(4))[0]
        if tex_hash != self.texture_hash:
            raise ValueError("Hash in material and texture files do not match")
        file.read(7) # Unknown, padding?
        self.texture_name = file.read(0x20).split(b'\x00')[0].decode()
        file.read(txrh_length - file.tell() + offset)
        
        # T3DS - Container for CTPK texture
        #magic = file.read(4)
        #if magic != b"TGTF":
        #    invalid_format("TGTF", file.tell(), magic) 
        #tgtf_length = struct.unpack("<I", file.read(4))[0]
        
        # Todo, EXT1 texture decompression for CTPK....

class ImportSanzaruModel(Operator, ImportHelper):
    bl_idname = "custom_import_scene.sanzaru2"
    bl_label = "Import"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".geo"
    filter_glob: bpy.props.StringProperty(
        default="*.geo",
        options={'HIDDEN'},
        maxlen=255,
    )
    
    filepath: StringProperty(subtype='FILE_PATH',)
    files: CollectionProperty(type=bpy.types.PropertyGroup)
    
    def execute(self, context):
        folder = os.path.dirname(os.path.abspath(self.filepath)) + "\\"
        filelist = os.listdir(folder)

        with open(self.filepath, "rb") as geo_file:
            geo = SanzaruGEOB(geo_file)

        # TODO: Screw this
        collection = bpy.data.collections.new(geo.name)
        bpy.context.scene.collection.children.link(collection) 
        layer_collection = bpy.context.view_layer.layer_collection.children[collection.name]
        bpy.context.view_layer.active_layer_collection = layer_collection

        if geo.bone_count:
            skel_obj = geo.make_skel()
        else:
            skel_obj = 0

        mes_file = find_file(folder, ".mes", geo.hash, 0x21)
        # Could not locate value for submesh count. Finding instead based on submesh header count instead
        mes_file.seek(0)
        mesh_count = len(findall(b'SMSH', mes_file.read()))
        mes_file.seek(0)

        # Mesh File Identifier
        magic = mes_file.read(4)
        if magic != b"MESH":
            invalid_format("MESH", mes_file.tell(), magic)
        mesh_length = struct.unpack("<i", mes_file.read(4))[0]

        # Mesh File Header
        magic = mes_file.read(4)
        if magic != b"MSHH":
            invalid_format("MSHH", file.tell(), magic)
        mshh_length = struct.unpack("<i", mes_file.read(4))[0]
        mshh_version = struct.unpack("<B", mes_file.read(1))[0]
        mes_file.read(0x10) # 4 Unknown floats
        mesh_hash = struct.unpack("<i", mes_file.read(4))[0]
        mes_file.read(3) # Byte alignment

        material_names = {}
        texture_names = {}

        # Create submeshes
        for i in range(mesh_count):
            submesh = SanzaruSubmesh(mes_file, geo)
            mesh_obj = submesh.make_mesh(geo, i)
            mesh_obj.scale *= submesh.vertex_scale
            if skel_obj:
                mesh_obj.parent = skel_obj
                bpy.ops.object.modifier_add(type='ARMATURE')
                bpy.data.objects[mesh_obj.name].modifiers["Armature"].object = skel_obj
            else:
                mesh_obj.rotation_euler = ((math.pi / 2),0,0)
            mat_hash = str(submesh.material_hash)
            if mat_hash not in material_names:
                mat_file = find_file(folder, ".mat", submesh.material_hash, 0x25)
                mat = SanzaruMaterial(mat_file)
                material = bpy.data.materials.new(mat.material_name)
                material_names.update({mat_hash:material.name}) # Assign Blender material name in case duplicates exist
                material.use_nodes = True
            
                main_node = material.node_tree.nodes["Principled BSDF"]
                #mat.texture_hash = mat.texture_color_hash
                # Find texture if not already found
                #tex_hash = str(mat.texture_hash)
                #if tex_hash not in texture_names:
                #    tex_file = find_file(folder, ".tex", mat.texture_hash, 0x11)
                #    mat.parse_tex(tex_file)
                #    # TODO: Import actual texture
                #    texture = bpy.data.images.new(mat.texture_name, 64, 64)
                #    texture = bpy.data.images.new(mat.texture_name.split(".")[0], 64, 64)
                #    texture.generated_color = (0.8,0.8,0.8,1)
                #    texture_name = texture.name
                #    texture_names.update({tex_hash:texture_name})
                #else:
                #    texture_name = texture_names[tex_hash]
                #    texture = bpy.data.textures.get(texture_name)
                if mat.texture_color_hash:
                    mat.texture_hash = mat.texture_color_hash
                    color_file = find_file(folder, ".tex", mat.texture_color_hash, 0x11)
                    mat.parse_tex(color_file)
                    color_node = material.node_tree.nodes.new(type='ShaderNodeTexImage')
                    color_node.image = bpy.data.images.load(os.path.dirname(self.filepath) + "\\" + mat.texture_name + ".dds")
                    material.node_tree.links.new(color_node.outputs['Color'], main_node.inputs['Base Color'])
                if mat.texture_specular_hash:
                    mat.texture_hash = mat.texture_specular_hash
                    spec_file = find_file(folder, ".tex", mat.texture_specular_hash, 0x11)
                    mat.parse_tex(spec_file)
                    color_node = material.node_tree.nodes.new(type='ShaderNodeTexImage')
                    color_node.image = bpy.data.images.load(os.path.dirname(self.filepath) + "\\" + mat.texture_name + ".dds")
                    color_node.image.colorspace_settings.name = 'Non-Color'
                    material.node_tree.links.new(color_node.outputs['Color'], main_node.inputs['Specular'])
                if mat.texture_normal_hash:
                    mat.texture_hash = mat.texture_normal_hash
                    norm_file = find_file(folder, ".tex", mat.texture_normal_hash, 0x11)
                    mat.parse_tex(norm_file)
                    color_node = material.node_tree.nodes.new(type='ShaderNodeTexImage')
                    color_node.image = bpy.data.images.load(os.path.dirname(self.filepath) + "\\" + mat.texture_name + ".dds")
                    color_node.image.colorspace_settings.name = 'Non-Color'
                    blue_node = material.node_tree.nodes.new(type='ShaderNodeMixRGB')
                    blue_node.blend_type = 'ADD'
                    blue_node.use_clamp = True
                    blue_node.inputs[0].default_value = 1
                    blue_node.inputs[2].default_value = (0, 0, 1, 1)
                    material.node_tree.links.new(color_node.outputs['Color'], blue_node.inputs['Color1'])
                    normal_node = material.node_tree.nodes.new(type='ShaderNodeNormalMap')
                    material.node_tree.links.new(blue_node.outputs[0], normal_node.inputs[1])
                    material.node_tree.links.new(normal_node.outputs[0], main_node.inputs['Normal'])
            else:
                material_name = material_names[mat_hash]
                material = bpy.data.materials.get(material_name)

            mesh_obj.data.materials.append(material)
            
        if skel_obj:
            skel_obj.rotation_euler = ((math.pi / 2),0,0)
        return {'FINISHED'}

def invalid_format(txt, loc, value):
    loc_hex = hex(loc)[:2] + hex(loc)[2:].upper() # For nicer readable hex offsets
    wrong_data = f"Unexpected magic bytes; expected {txt} chunk at {loc_hex}, actual value {value}"
    eof = "Unexpected end of file"
    if not value:
        raise ValueError(eof)
    else:
        raise ValueError(wrong_data)

def find_file(folder, suffix, target_hash, offset): # Find desired file based on hash
    filelist = os.listdir(folder)
    for file in filelist:
        if file.endswith(suffix):
            with open(folder + file, "rb") as file_test:
                file_test.read(offset)
                file_hash = struct.unpack("<i", file_test.read(4))[0]
                if file_hash == target_hash:
                    print(file)
                    file_test.seek(0)
                    file_contents = file_test.read()
                    return BytesIO(file_contents)
    raise ValueError(f"Could not find associated {suffix} file")

def menu_func_import(self, context):
    self.layout.operator(ImportSanzaruModel.bl_idname, text="Sly Cooper/Sanzaru Model (.geo)")

def register():
    bpy.utils.register_class(ImportSanzaruModel)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.utils.unregister_class(ImportSanzaruModel)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

if __name__ == "__main__":
    register()

