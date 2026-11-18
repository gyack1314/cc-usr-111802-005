"""按各自时间尺度与来源版本比较跨季方案。

四类输入各按自己的时间尺度参与计算, 互不覆盖:

* 冰川融水估计 (meltwater): 月值估计, 允许缺观测;
* 来水观测 (inflow): 实际水文观测;
* 库容 (storage): 区间记录, 按 valid_from/valid_to 覆盖月份取值;
* 灌溉需求 (demand): 月值;
* 方案 (scenario): 将各类来源版本与单位绑定为一个具体口径。

每份记录带 unit, 一个方案内各类输入必须同单位才能相加; 单位不同的方案
仍可各自给出承诺, 但两方案之间只标“不可直接比较”, 不相减。所有计算只使用
known_at 之前收到的版本, 因此历史方案可按当时已知资料复现。

差异分为两类: 来源版本变化但该月可供水量与缺口不变, 记作“估计口径改变”;
可供水量、缺口或可供水区间改变时, 记作“承诺变化”, 并列出受影响月份。
"""
from datetime import date
from .evidence import all_records

KINDS=('meltwater','inflow','storage','demand')

def _month(value,label='月份'):
    if not isinstance(value,str) or len(value)!=7 or value[4]!='-':
        raise ValueError(f'{label}必须是 YYYY-MM')
    year,month=value.split('-')
    try:parsed=date(int(year),int(month),1)
    except ValueError:raise ValueError(f'{label}必须是 YYYY-MM: {value}') from None
    return parsed.strftime('%Y-%m')

def months_between(start,end):
    first=_month(start,'开始月份');last=_month(end,'结束月份')
    if last<first:raise ValueError('结束月份不能早于开始月份')
    out=[];year,month=int(first[:4]),int(first[5:])
    while True:
        marker=f'{year:04d}-{month:02d}'
        out.append(marker)
        if marker==last:break
        month+=1
        if month>12:year+=1;month=1
    return out

def _covers(record,month,interval):
    start=_month(record['valid_from'][:7],'记录有效起始')
    raw_end=record.get('valid_to')
    if raw_end:
        end=_month(raw_end[:7],'记录有效结束')
    elif interval:
        end=None
    else:
        # 月值只代表自己那个月; 缺月不能被上月的值顺延填补。
        end=start
    return start<=month and (end is None or month<=end)

def _pick(rows,month,interval):
    covering=[row for row in rows if _covers(row,month,interval)]
    if not covering:return None
    covering.sort(key=lambda row:(row['valid_from'],row['source']['received_at'],row['revision']))
    return covering[-1]

def _require(payload,key,where):
    if key not in payload:raise ValueError(f'{where}缺少 {key}')
    return payload[key]

def _validate(row,unit):
    payload=row['payload'];where='记录 '+row['id']
    row_unit=_require(payload,'unit',where)
    if row_unit!=unit:raise ValueError(f'{where}单位为 {row_unit}, 与方案单位 {unit} 不符')
    value=_require(payload,'value',where)
    if not isinstance(value,(int,float)) or isinstance(value,bool):
        raise ValueError(f'{where}数值不合法')
    return value

def _monthly(rows,months,unit,interval=False):
    """按月取值。interval=True 的类别按有效区间覆盖月份, 否则只取本月记录。"""
    picked={};sources=[]
    for month in months:
        row=_pick(rows,month,interval)
        if row is None:continue
        picked[month]=_validate(row,unit)
        sources.append({'month':month,'record':row['id'],'revision':row['revision'],
            'source':row['source']['name'],'document':row['source']['document'],
            'document_revision':row['source']['revision'],
            'sha256':row['source']['sha256'],
            'received_at':row['source']['received_at'],
            'valid_from':row['valid_from'],'valid_to':row.get('valid_to')})
    return picked,sources

def _latest(rows):
    latest={}
    for row in rows:
        marker=(row['source']['received_at'],row['revision'])
        if row['id'] not in latest or marker>latest[row['id']][0]:
            latest[row['id']]=(marker,row)
    return {key:item[1] for key,item in latest.items()}

def _resolve(db,known_at,kind,scenario):
    """未绑定的记录取 known_at 前最新版本; 绑定者固定为该版本。

    只固定部分记录时, 其余记录仍按当时最新版本参与, 便于按旧口径复现。
    """
    pins=scenario.get(kind,{})
    if not isinstance(pins,dict):raise ValueError(f'方案 {kind} 必须按记录标识绑定版本')
    rows=all_records(db,kind,known_at)
    selected=_latest(rows)
    for record_id,revision in pins.items():
        match=[row for row in rows if row['id']==record_id and row['revision']==revision]
        if not match:raise ValueError(f'方案要求的 {kind} {record_id}@{revision} 在 {known_at} 尚不可知')
        selected[record_id]=match[0]
    return [selected[key] for key in sorted(selected)]

def _estimate_against_observation(estimate,observed,months):
    rows=[]
    for month in months:
        if month not in estimate or month not in observed:
            rows.append({'month':month,'estimate':estimate.get(month),
                'observed':observed.get(month),'difference':None,'status':'缺一侧资料'})
        else:
            rows.append({'month':month,'estimate':estimate[month],
                'observed':observed[month],'difference':observed[month]-estimate[month],
                'status':'可比'})
    return rows

def compare_season(db,known_at,scenario):
    """在 known_at 已知资料下比较一个跨季方案的可用水与灌溉承诺。

    scenario 形如::

        {'id':'2027-irrigation','start':'2027-04','end':'2027-09','unit':'m3',
         'meltwater':{'melt-1':'r1'},   # 可选; 固定来源版本
         'inflow':{}, 'storage':{}, 'demand':{}}

    未绑定的类别取 known_at 前的最新版本; 绑定后固定为指定版本, 使方案可复现。
    """
    if not isinstance(scenario,dict):raise ValueError('方案必须是对象')
    scenario_id=_require(scenario,'id','方案')
    unit=_require(scenario,'unit',f'方案 {scenario_id}')
    months=months_between(_require(scenario,'start',f'方案 {scenario_id}'),
                          _require(scenario,'end',f'方案 {scenario_id}'))
    resolved={kind:_resolve(db,known_at,kind,scenario) for kind in KINDS}
    estimate,melt_sources=_monthly(resolved['meltwater'],months,unit)
    observed,inflow_sources=_monthly(resolved['inflow'],months,unit)
    storage,storage_sources=_monthly(resolved['storage'],months,unit,interval=True)
    demand,demand_sources=_monthly(resolved['demand'],months,unit)

    rows=[];supply=[]
    total_available=total_allocated=total_demand=0
    incomplete=False
    for month in months:
        melt=estimate.get(month);rain=observed.get(month)
        available=None if melt is None and rain is None else (melt or 0)+(rain or 0)
        cap=storage.get(month);need=demand.get(month)
        if available is None or cap is None:
            incomplete=True
            allocation={'month':month,'available':available,'capacity':cap,
                'allocated':None,'demand':need,'demand_met':None,'shortfall':None,
                'unused':None,'status':'缺库容或来水资料'}
        else:
            allocated=min(available,cap)
            shortfall=max(need-allocated,0) if need is not None else None
            if need is None:incomplete=True
            total_available+=available;total_allocated+=allocated
            total_demand+=need or 0
            allocation={'month':month,'available':available,'capacity':cap,
                'allocated':allocated,'demand':need,
                'demand_met':None if need is None else need-shortfall,
                'shortfall':shortfall,'unused':max(cap-allocated,0),
                'status':'需求缺失' if need is None else ('可满足' if not shortfall else '不足')}
        supply.append(allocation)
        rows.append({'month':month,'meltwater_estimate':melt,'inflow':rain,
            'storage_capacity':cap,'irrigation_demand':need})

    shortfall=max(total_demand-total_allocated,0)
    status='不足' if shortfall else ('资料不全' if incomplete else '可满足')
    return {'scenario':scenario_id,'known_at':known_at,'unit':unit,
        'start':months[0],'end':months[-1],'months':rows,
        'meltwater_vs_inflow':_estimate_against_observation(estimate,observed,months),
        'supply':supply,
        'season':{'available':total_available,'allocated':total_allocated,
                  'demand':total_demand,'shortfall':shortfall,'status':status},
        'sources':{'meltwater':melt_sources,'inflow':inflow_sources,
                   'storage':storage_sources,'demand':demand_sources}}

def input_versions(report):
    """方案实际使用的来源版本, 用于判断两次比较之间的资料修订。"""
    return {kind:[(row['document'],row['document_revision'],row['sha256'])
                  for row in report['sources'][kind]] for kind in KINDS}

def changed_inputs(before,after):
    left=input_versions(before);right=input_versions(after)
    return [kind for kind in KINDS if left[kind]!=right[kind]]

def _classify(before_supply,after_supply,changed,comparable=True):
    """对一个月给出差异性质。

    “估计口径改变”指来源版本或单位变了, 但该月实际可供水与缺口不变;
    一旦可供水量、缺口或可供水区间改变, 该月承诺受影响, 归入“承诺变化”
    或区间增减, 即使起因只是融水估计被修订。
    """
    if not comparable:return '不可直接比较'
    if before_supply is None or after_supply is None:return '月份范围不同'
    old_basis=before_supply['allocated'] is not None
    new_basis=after_supply['allocated'] is not None
    if old_basis!=new_basis:return '可供水区间缩短' if old_basis else '可供水区间恢复'
    if not old_basis:
        return '估计口径改变' if set(changed)<={'meltwater'} and changed else '无可比承诺'
    same_commitment=(before_supply['allocated']==after_supply['allocated']
                     and before_supply['shortfall']==after_supply['shortfall'])
    if same_commitment:
        return '估计口径改变' if set(changed)<={'meltwater'} and changed else '同口径无差异'
    return '承诺变化'

AFFECTED_KINDS={'承诺变化','可供水区间缩短','可供水区间恢复'}

def compare_scenarios(db,known_at,scenarios):
    """比较同一 known_at 下的多个方案。

    只改变融水估计版本或单位的方案之间, 差异属于估计口径; 库容、来水或
    需求不同则属于承诺变化。月份或单位不一致的方案不做数值比较。
    """
    if not isinstance(scenarios,list) or not scenarios:
        raise ValueError('至少需要一个方案')
    results=[compare_season(db,known_at,scenario) for scenario in scenarios]
    ids=[row['scenario'] for row in results]
    if len(set(ids))!=len(ids):raise ValueError('方案标识不能重复')
    index={row['scenario']:row for row in results}
    pairs=[]
    for left_pos in range(len(results)):
        for right_pos in range(left_pos+1,len(results)):
            left=results[left_pos];right=results[right_pos]
            same_months=[row['month'] for row in left['months']]==[row['month'] for row in right['months']]
            changed=changed_inputs(left,right)
            comparable=same_months and left['unit']==right['unit']
            right_by_month={row['month']:row for row in right['supply']}
            by_month={}
            for left_supply in left['supply']:
                right_supply=right_by_month.get(left_supply['month'])
                old=left_supply['shortfall'];new=right_supply['shortfall'] if right_supply else None
                kind=_classify(left_supply,right_supply,changed,comparable)
                by_month[left_supply['month']]={'shortfall_before':old,
                    'shortfall_after':new,
                    'allocated_before':left_supply['allocated'],
                    'allocated_after':right_supply['allocated'] if right_supply else None,
                    'change':None if old is None or new is None else new-old,
                    'kind':kind}
            pairs.append({'before':left['scenario'],'after':right['scenario'],
                'changed_inputs':changed,
                'affected_months':sorted(month for month,row in by_month.items()
                                         if row['kind'] in AFFECTED_KINDS),
                'estimate_only_months':sorted(month for month,row in by_month.items()
                                              if row['kind']=='估计口径改变'),
                'months':by_month})
    return {'known_at':known_at,'scenarios':index,'comparisons':pairs}

def pins_from_report(report):
    """从已生成方案中取出各记录的来源版本, 作为复现该方案的绑定。"""
    pins={}
    for kind in KINDS:
        bound={}
        for row in report['sources'][kind]:bound[row['record']]=row['revision']
        pins[kind]=bound
    return pins

def compare_reports(before,after):
    """比较同一方案在两个已知时点的承诺, 说明受影响月份与差异原因。"""
    before_months={row['month']:row for row in before['months']}
    after_months={row['month']:row for row in after['months']}
    changed=changed_inputs(before,after)
    same_months=sorted(before_months)==sorted(after_months)
    comparable=same_months and before['scenario']==after['scenario'] and before['unit']==after['unit']
    months=[]
    for month in sorted(before_months.keys()|after_months.keys()):
        old_supply=next((s for s in before['supply'] if s['month']==month),None)
        new_supply=next((s for s in after['supply'] if s['month']==month),None)
        old=old_supply['shortfall'] if old_supply else None
        new=new_supply['shortfall'] if new_supply else None
        kind=_classify(old_supply,new_supply,changed,comparable)
        months.append({'month':month,'before':before_months.get(month),
            'after':after_months.get(month),'shortfall_before':old,
            'shortfall_after':new,
            'allocated_before':old_supply['allocated'] if old_supply else None,
            'allocated_after':new_supply['allocated'] if new_supply else None,
            'change':None if old is None or new is None else new-old,'kind':kind})
    return {'scenario':before['scenario'],'before_known_at':before['known_at'],
        'after_known_at':after['known_at'],'changed_inputs':changed,
        'affected_months':sorted(row['month'] for row in months if row['kind'] in AFFECTED_KINDS),
        'estimate_only_months':sorted(row['month'] for row in months if row['kind']=='估计口径改变'),
        'months':months}
