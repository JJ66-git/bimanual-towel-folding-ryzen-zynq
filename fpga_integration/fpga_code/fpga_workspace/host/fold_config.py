"""B35 canonical, integer-only configuration identity (wire specification section 12)."""
import hashlib
import json
from pathlib import Path
import zlib

ROOT=Path(__file__).resolve().parents[1]

def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate config key: '+key)
        result[key]=value
    return result

def canonical(profile):
    def visit(value):
        if type(value) is dict:
            for key,item in value.items():
                if type(key) is not str:raise ValueError('non-string config key')
                visit(item)
        elif type(value) is list:
            for item in value:visit(item)
        elif value is not None and type(value) not in (str,int,bool):raise ValueError('config must not contain floats or non-JSON types')
    visit(profile)
    return json.dumps(profile,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def identity(profile):
    data=canonical(profile)
    return {'crc32':zlib.crc32(data)&0xffffffff,'sha256':hashlib.sha256(data).hexdigest(),'canonical_bytes':len(data)}

def parameters(p):
    def take(section,key):
        value=p[section][key] if section else p[key]
        if type(value) is not int:raise ValueError('integer parameter required: '+str(section)+'.'+key)
        return value
    result={'BUILD_ID':take(None,'build_id'),'PHASE_CONFIG_CRC':identity(p)['crc32'],'FCLK_HZ':take(None,'fclk_hz'),
            'IMAGE_WIDTH':take('camera','width'),'IMAGE_HEIGHT':take('camera','height')}
    for prefix,section in [('STAGE','stage'),('RETREAT','retreat'),('TIMING','timing'),('VISION','vision')]:
        for key in p[section]:result[prefix+'_'+key.upper()]=take(section,key)
    hz=result['FCLK_HZ']
    for name,key in [('LOCAL_TIMEOUT_CYCLES','local_joint_timeout_us'),('EVENT_TIMEOUT_CYCLES','event_window_us')]:
        product=hz*take('timing',key)
        if product%1000000:raise ValueError('timeout is not an integer number of cycles')
        result[name]=product//1000000
    return result

def validate(profile):
    canonical(profile)
    if profile['schema']!='fold_verify_profile_v1' or profile['build_id']!=0x46504735:raise ValueError('wrong config schema/build')
    if profile['protocol_versions']!={'FPG1':3,'FVE1':1}:raise ValueError('wrong wire version')
    if profile['fclk_hz']!=50000000 or profile['unit_scale']!=1000000:raise ValueError('unsupported clock/unit scale')
    # This release only qualifies synthetic feature vectors, never camera calibration.
    if profile['calibration_status']!='synthetic_test' or not profile['calibration_id']:raise ValueError('B35 revision has no field calibration')
    if profile['feature_algorithm']!={'version':'synthetic_feature_vectors_v1','hsv_lower':None,'hsv_upper':None,'morphology':None,'real_image_extraction_enabled':False}:raise ValueError('unqualified image algorithm')
    if profile['field_validation_evidence']!=[]:raise ValueError('field evidence not qualified by this release')
    c=profile['camera']
    if c['role']!='middle' or c['roi']!=[0,0,c['width'],c['height']] or c['scale_numerator']!=1 or c['scale_denominator']!=1:raise ValueError('unsupported camera geometry')
    values=parameters(profile)
    if not 1<=values['IMAGE_WIDTH']<=65535 or not 1<=values['IMAGE_HEIGHT']<=65535:raise ValueError('invalid dimensions')
    for key,value in values.items():
        if key!='BUILD_ID' and key!='PHASE_CONFIG_CRC' and not -(1<<31)<=value<=(1<<31)-1:raise ValueError('parameter overflow: '+key)
    for section,lo,hi in [('stage','fold_j2_min','fold_j2_max'),('stage','fold_j3_min','fold_j3_max'),('retreat','j2_min','j2_max'),('retreat','j3_min','j3_max')]:
        if profile[section][lo]>profile[section][hi]:raise ValueError('inverted stage bounds')
    for metric in ['area','rectangularity','dark']:
        lo=profile['vision'][metric+'_min_bp'];hi=profile['vision'][metric+'_max_bp']
        if not 0<=lo<=hi<=65535:raise ValueError('invalid vision interval')
    for section,key in [('stage','min_samples'),('retreat','min_samples')]:
        if not 1<=profile[section][key]<=7:raise ValueError('sample count width overflow')
    if not 1<=profile['vision']['min_frames']<=255:raise ValueError('frame count width overflow')
    for key,value in profile['timing'].items():
        if value<=0:raise ValueError('invalid time: '+key)
    return profile

def load(path=None):
    path=Path(path) if path else ROOT/'config/fold_verify_profile.json'
    result=json.loads(path.read_text(),object_pairs_hook=_object,parse_constant=lambda v:(_ for _ in ()).throw(ValueError(v)))
    return validate(result)

def header(profile):
    lines=['// Generated from canonical fold_verify_profile.json; do not edit.', '`ifndef FOLD_VERIFY_PARAMS_SVH','`define FOLD_VERIFY_PARAMS_SVH']
    for key,value in parameters(validate(profile)).items():
        token=("32'h%08x"%value) if key in ('BUILD_ID','PHASE_CONFIG_CRC') else ("(-32'sd%d)"%abs(value) if value<0 else "32'sd%d"%value)
        lines.append('`define FV_'+key+' '+token)
    return '\n'.join(lines+['`endif',''])
