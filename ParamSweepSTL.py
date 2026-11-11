# ParamSweepSTL
# Fusion script: sweep one or two parameters through lists of values and export
# STL / 3MF / STEP for every variant. Original values are restored at the end.
# See README.md for the full description.
#
# Values field syntax (mix freely, separated by commas):
#   10, 12.5, 15        explicit numbers, in the parameter's own unit
#   10:20:2             range start:stop:step, stop included -> 10 12 14 16 18 20
#   25.4 mm / 2         anything else is passed to Fusion as a raw expression
#
# File name tokens: {doc} {param} {value} {param2} {value2} {index} {body}

import adsk.core
import adsk.fusion
import json
import math
import os
import re
import traceback

CMD_ID = 'paramSweepStlExportCmd'
CMD_NAME = 'Parameter Sweep Export'
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'last_settings.json')

NONE_LABEL = '(none)'
SCOPE_WHOLE = 'Whole design (one file per variant)'
SCOPE_BODIES = 'Each visible body separately'
COMBINE_GRID = 'Every combination (grid)'
COMBINE_PAIRED = 'Paired (1st with 1st, 2nd with 2nd...)'
REFINEMENTS = ['High', 'Medium', 'Low']
MAX_VARIANTS = 2000
DEFAULT_TEMPLATE = '{doc}_{param}_{value}'

_app = None
_ui = None
_handlers = []
_param_names = []   # parameter names, same order as the dropdown items

_NUM = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
_RE_NUM = re.compile(r'^' + _NUM + r'$')
_RE_RANGE = re.compile(r'^(' + _NUM + r')\s*:\s*(' + _NUM + r')\s*:\s*(' + _NUM + r')$')


# ----------------------------------------------------------------- helpers

def fmt_num(v):
    """12.0 -> '12', 12.50 -> '12.5' (no exponent notation)."""
    s = ('%.6f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('', '-0') else s


def parse_values(text):
    """Return a list of (label, kind, raw) where kind is 'num' or 'expr'.
    Raises ValueError with a readable message on bad input."""
    out = []
    for token in re.split(r'[,;\n]+', text):
        token = token.strip()
        if not token:
            continue
        m = _RE_RANGE.match(token)
        if m:
            start, stop, step = (float(g) for g in m.groups())
            if step == 0:
                raise ValueError('Step cannot be 0 in "%s"' % token)
            if (stop - start) * step < 0:
                step = -step
            count = int(math.floor((stop - start) / step + 1e-9)) + 1
            if count > MAX_VARIANTS:
                raise ValueError('"%s" would create %d variants' % (token, count))
            for i in range(count):
                s = fmt_num(round(start + i * step, 9))
                out.append((s, 'num', s))
        elif _RE_NUM.match(token):
            s = fmt_num(float(token))
            out.append((s, 'num', s))
        else:
            out.append((token, 'expr', token))
    if not out:
        raise ValueError('No values given')
    return out


def build_variants(values1, values2, combine):
    """List of variants; each variant is a list with one value tuple per
    parameter. values2 is None when only one parameter is swept."""
    if values2 is None:
        return [[v] for v in values1]
    if combine == COMBINE_PAIRED:
        if len(values1) != len(values2):
            raise ValueError('Paired mode needs the same number of values for both '
                             'parameters (%d vs %d)' % (len(values1), len(values2)))
        return [[a, b] for a, b in zip(values1, values2)]
    if len(values1) * len(values2) > MAX_VARIANTS:
        raise ValueError('%d x %d combinations is too many'
                         % (len(values1), len(values2)))
    return [[a, b] for a in values1 for b in values2]


def safe_name(s):
    s = re.sub(r'[^\w.\-+]+', '_', str(s)).strip('_')
    return s or 'x'


def doc_name():
    return re.sub(r'\s+v\d+$', '', _app.activeDocument.name)


def load_settings():
    try:
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def list_parameters(design, include_model):
    """User parameters first, then (optionally) model parameters."""
    params = [p for p in design.userParameters]
    if include_model:
        user = set(p.name for p in params)
        params += [p for p in design.allParameters if p.name not in user]
    return params


def fill_param_dropdowns(dd1, dd2, design, include_model, pref1=None, pref2=None):
    """dd1 lists the parameters; dd2 lists '(none)' followed by the same."""
    global _param_names
    params = list_parameters(design, include_model)
    _param_names = [p.name for p in params]
    sel1 = pref1 if pref1 in _param_names else (
        _param_names[0] if _param_names else None)
    sel2 = pref2 if (pref2 in _param_names and pref2 != sel1) else None
    dd1.listItems.clear()
    dd2.listItems.clear()
    dd2.listItems.add(NONE_LABEL, sel2 is None, '')
    for p in params:
        label = '%s  =  %s' % (p.name, p.expression)
        dd1.listItems.add(label, p.name == sel1, '')
        dd2.listItems.add(label, p.name == sel2, '')


def selected_params(inputs):
    """(name1, name2) from the two dropdowns; name2 is '' when not used."""
    s1 = inputs.itemById('param').selectedItem
    s2 = inputs.itemById('param2').selectedItem
    name1 = _param_names[s1.index] if s1 else ''
    name2 = _param_names[s2.index - 1] if (s2 and s2.index > 0) else ''
    return name1, name2


def timeline_errors(design):
    """Names of timeline features that are in an error state."""
    bad = []
    try:
        tl = design.timeline
        err = adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState
        for i in range(tl.count):
            item = tl.item(i)
            if item.healthState == err:
                bad.append(item.name)
    except Exception:
        pass
    return bad


def collect_bodies(root):
    """(name, body) for every visible solid body, including nested components."""
    found = []
    for b in root.bRepBodies:
        if b.isSolid and b.isVisible:
            found.append((b.name, b))
    for occ in root.allOccurrences:
        for b in occ.bRepBodies:          # proxies, positioned in the assembly
            if b.isSolid and b.isVisible:
                found.append(('%s_%s' % (occ.name, b.name), b))
    seen, out = {}, []                    # make names unique
    for name, b in found:
        n = seen.get(name, 0)
        seen[name] = n + 1
        out.append((name if n == 0 else '%s_%d' % (name, n + 1), b))
    return out


def _refinement(name):
    R = adsk.fusion.MeshRefinementSettings
    return {
        'High': R.MeshRefinementHigh,
        'Medium': R.MeshRefinementMedium,
        'Low': R.MeshRefinementLow,
    }.get(name, R.MeshRefinementMedium)


def export_stl(design, geometry, path, refinement, binary):
    em = design.exportManager
    opts = em.createSTLExportOptions(geometry, path)
    opts.sendToPrintUtility = False
    opts.isBinaryFormat = binary
    opts.meshRefinement = _refinement(refinement)
    em.execute(opts)


def export_3mf(design, geometry, path, refinement):
    em = design.exportManager
    opts = em.createC3MFExportOptions(geometry, path)
    opts.sendToPrintUtility = False
    opts.meshRefinement = _refinement(refinement)
    em.execute(opts)


def export_step(design, path):
    # STEP is always the whole design: the API does not take a single body here.
    em = design.exportManager
    opts = em.createSTEPExportOptions(path, design.rootComponent)
    em.execute(opts)


def build_basename(template, names, labels, index, body=None):
    """File name without extension."""
    name = template or DEFAULT_TEMPLATE
    if len(names) > 1 and '{value2}' not in name:
        name += '_{param2}_{value2}'
    if body is not None and '{body}' not in name:
        name += '_{body}'
    name2 = names[1] if len(names) > 1 else ''
    label2 = labels[1] if len(labels) > 1 else ''
    name = (name.replace('{doc}', safe_name(doc_name()))
                .replace('{param2}', safe_name(name2) if name2 else '')
                .replace('{value2}', safe_name(label2) if name2 else '')
                .replace('{param}', safe_name(names[0]))
                .replace('{value}', safe_name(labels[0]))
                .replace('{index}', '%03d' % index)
                .replace('{body}', safe_name(body) if body is not None else ''))
    name = re.sub(r'[<>:"/\\|?*]+', '_', name)
    name = re.sub(r'_{2,}', '_', name).strip(' ._')
    return name or 'export'


# ------------------------------------------------------------ the sweep

def run_sweep(design, cfg):
    names = [cfg['param']] + ([cfg['param2']] if cfg['param2'] else [])
    params = []
    for n in names:
        p = design.allParameters.itemByName(n)
        if not p:
            _ui.messageBox('Parameter "%s" not found.' % n)
            return
        params.append(p)

    try:
        v1 = parse_values(cfg['values'])
        v2 = parse_values(cfg['values2']) if cfg['param2'] else None
        variants = build_variants(v1, v2, cfg['combine'])
    except ValueError as e:
        _ui.messageBox(str(e), CMD_NAME)
        return

    folder = cfg['folder']
    if not os.path.isdir(folder):
        os.makedirs(folder)

    originals = [p.expression for p in params]
    units = [p.unit or '' for p in params]
    per_body = cfg['scope'] == SCOPE_BODIES
    exported, failed = [], []
    cancelled = False

    pd = _ui.createProgressDialog()
    pd.cancelButtonText = 'Cancel'
    pd.isCancelButtonShown = True
    pd.show(CMD_NAME, 'Starting...', 0, len(variants), 0)

    try:
        for i, variant in enumerate(variants, start=1):
            if pd.wasCancelled:
                cancelled = True
                break
            labels = [v[0] for v in variant]
            tag = ', '.join('%s=%s' % (n, l) for n, l in zip(names, labels))
            pd.message = '%s   (%d of %d)' % (tag, i, len(variants))

            # apply the values
            rejected = None
            for p, unit, (label, kind, raw) in zip(params, units, variant):
                expr = ('%s %s' % (raw, unit)).strip() if kind == 'num' else raw
                try:
                    if p.expression != expr:
                        p.expression = expr
                except Exception:
                    rejected = 'Fusion rejected %s = "%s"' % (p.name, expr)
                    break
            if rejected:
                failed.append((tag, rejected))
                pd.progressValue = i
                continue
            adsk.doEvents()

            errs = timeline_errors(design)
            if errs and cfg['skip_errors']:
                failed.append((tag, 'feature error: ' + ', '.join(errs[:3])))
                pd.progressValue = i
                continue

            # export
            try:
                base = build_basename(cfg['template'], names, labels, i)
                if cfg['stl'] or cfg['3mf']:
                    if per_body:
                        bodies = collect_bodies(design.rootComponent)
                        if not bodies:
                            raise RuntimeError('no visible solid bodies')
                        targets = [(build_basename(cfg['template'], names, labels, i, bn), b)
                                   for bn, b in bodies]
                    else:
                        targets = [(base, design.rootComponent)]
                    for fn, geom in targets:
                        if cfg['stl']:
                            export_stl(design, geom, os.path.join(folder, fn + '.stl'),
                                       cfg['refinement'], cfg['binary'])
                            exported.append(fn + '.stl')
                        if cfg['3mf']:
                            export_3mf(design, geom, os.path.join(folder, fn + '.3mf'),
                                       cfg['refinement'])
                            exported.append(fn + '.3mf')
                if cfg['step']:
                    export_step(design, os.path.join(folder, base + '.step'))
                    exported.append(base + '.step')
                if errs:
                    failed.append((tag, 'exported WITH feature errors: '
                                   + ', '.join(errs[:3])))
            except Exception as e:
                failed.append((tag, 'export failed: %s' % e))
            pd.progressValue = i
    finally:
        for p, orig in zip(params, originals):     # always put the design back
            try:
                if p.expression != orig:
                    p.expression = orig
            except Exception:
                failed.append(('restore', 'could not restore %s = %s' % (p.name, orig)))
        try:
            adsk.doEvents()
        except Exception:
            pass
        pd.hide()

    msg = '%d file(s) written to:\n%s' % (len(exported), folder)
    if cancelled:
        msg += '\n\nCancelled before finishing.'
    if failed:
        msg += '\n\nProblems (%d):\n' % len(failed)
        msg += '\n'.join('  %s: %s' % f for f in failed[:15])
        if len(failed) > 15:
            msg += '\n  ...'
    msg += '\n\nRestored: ' + ', '.join('%s = %s' % (p.name, o)
                                        for p, o in zip(params, originals))
    _ui.messageBox(msg, CMD_NAME)


# --------------------------------------------------------- command dialog

def read_inputs(inputs):
    name1, name2 = selected_params(inputs)
    return {
        'param': name1,
        'values': inputs.itemById('values').value,
        'param2': name2,
        'values2': inputs.itemById('values2').value,
        'combine': inputs.itemById('combine').selectedItem.name,
        'include_model': inputs.itemById('includeModel').value,
        'folder': inputs.itemById('folder').value.strip(),
        'template': inputs.itemById('template').value.strip() or DEFAULT_TEMPLATE,
        'stl': inputs.itemById('fmtStl').value,
        '3mf': inputs.itemById('fmt3mf').value,
        'step': inputs.itemById('fmtStep').value,
        'scope': inputs.itemById('scope').selectedItem.name,
        'refinement': inputs.itemById('refinement').selectedItem.name,
        'binary': inputs.itemById('binary').value,
        'skip_errors': inputs.itemById('skipErrors').value,
    }


def update_enabled(inputs):
    """Grey out controls that do not apply to the current choices."""
    _, name2 = selected_params(inputs)
    inputs.itemById('values2').isEnabled = bool(name2)
    inputs.itemById('combine').isEnabled = bool(name2)
    stl = inputs.itemById('fmtStl').value
    mesh = stl or inputs.itemById('fmt3mf').value
    inputs.itemById('binary').isEnabled = stl
    inputs.itemById('refinement').isEnabled = mesh
    inputs.itemById('scope').isEnabled = mesh


def add_dropdown(inputs, id_, label, items, current):
    dd = inputs.addDropDownCommandInput(
        id_, label, adsk.core.DropDownStyles.TextListDropDownStyle)
    if current not in items:
        current = items[0]
    for name in items:
        dd.listItems.add(name, name == current, '')
    return dd


class CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            design = adsk.fusion.Design.cast(_app.activeProduct)
            cmd = args.command
            cmd.okButtonText = 'Export'
            cmd.setDialogInitialSize(480, 620)
            inputs = cmd.commandInputs
            s = load_settings()
            style = adsk.core.DropDownStyles.TextListDropDownStyle

            include_model = bool(s.get('include_model', False))
            if design.userParameters.count == 0:
                include_model = True

            # --- parameters
            dd1 = inputs.addDropDownCommandInput('param', 'Parameter 1', style)
            inputs.addStringValueInput('values', 'Values 1', s.get('values', '10, 12, 15'))
            dd2 = inputs.addDropDownCommandInput('param2', 'Parameter 2', style)
            inputs.addStringValueInput('values2', 'Values 2', s.get('values2', '1, 2'))
            fill_param_dropdowns(dd1, dd2, design, include_model,
                                 s.get('param'), s.get('param2'))
            add_dropdown(inputs, 'combine', 'Combine',
                         [COMBINE_GRID, COMBINE_PAIRED], s.get('combine', COMBINE_GRID))
            inputs.addTextBoxCommandInput(
                'help', '',
                'Values: comma separated, in the parameter\'s unit. '
                'Ranges as <b>start:stop:step</b>, e.g. <b>10:20:2</b>.', 2, True)
            inputs.addBoolValueInput('includeModel', 'Show model parameters (d1, d2...)',
                                     True, '', include_model)

            # --- output
            inputs.addStringValueInput(
                'folder', 'Output folder',
                s.get('folder', os.path.join(os.path.expanduser('~'), 'Desktop', 'param_sweep')))
            btn = inputs.addBoolValueInput('browse', 'Choose folder', False, '', False)
            btn.text = 'Browse...'
            inputs.addStringValueInput('template', 'File name',
                                       s.get('template', DEFAULT_TEMPLATE))
            inputs.addTextBoxCommandInput(
                'help2', '',
                'Tokens: {doc} {param} {value} {param2} {value2} {index} {body}', 1, True)

            # --- formats
            inputs.addBoolValueInput('fmtStl', 'Export STL', True, '', bool(s.get('stl', True)))
            inputs.addBoolValueInput('fmt3mf', 'Export 3MF', True, '', bool(s.get('3mf', False)))
            inputs.addBoolValueInput('fmtStep', 'Export STEP (whole design)', True, '',
                                     bool(s.get('step', False)))
            add_dropdown(inputs, 'scope', 'STL / 3MF contain',
                         [SCOPE_WHOLE, SCOPE_BODIES], s.get('scope', SCOPE_WHOLE))
            add_dropdown(inputs, 'refinement', 'Mesh refinement',
                         REFINEMENTS, s.get('refinement', 'Medium'))
            inputs.addBoolValueInput('binary', 'Binary STL', True, '',
                                     bool(s.get('binary', True)))
            inputs.addBoolValueInput('skipErrors', 'Skip values that break the model',
                                     True, '', bool(s.get('skip_errors', True)))
            update_enabled(inputs)

            for cls, event in ((ExecuteHandler, cmd.execute),
                               (InputChangedHandler, cmd.inputChanged),
                               (ValidateHandler, cmd.validateInputs),
                               (DestroyHandler, cmd.destroy)):
                h = cls()
                event.add(h)
                _handlers.append(h)
        except Exception:
            _ui.messageBox('Failed:\n' + traceback.format_exc())


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            inputs = args.inputs
            changed = args.input
            if changed.id == 'browse':
                dlg = _ui.createFolderDialog()
                dlg.title = 'Folder for the exported files'
                if dlg.showDialog() == adsk.core.DialogResults.DialogOK:
                    inputs.itemById('folder').value = dlg.folder
            elif changed.id == 'includeModel':
                design = adsk.fusion.Design.cast(_app.activeProduct)
                keep1, keep2 = selected_params(inputs)
                fill_param_dropdowns(inputs.itemById('param'), inputs.itemById('param2'),
                                     design, changed.value, keep1, keep2)
                update_enabled(inputs)
            elif changed.id in ('param2', 'fmtStl', 'fmt3mf', 'fmtStep'):
                update_enabled(inputs)
        except Exception:
            _ui.messageBox('Failed:\n' + traceback.format_exc())


class ValidateHandler(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        try:
            inputs = args.inputs
            name1, name2 = selected_params(inputs)
            ok = bool(name1) and name1 != name2
            ok = ok and bool(inputs.itemById('folder').value.strip())
            ok = ok and (inputs.itemById('fmtStl').value
                         or inputs.itemById('fmt3mf').value
                         or inputs.itemById('fmtStep').value)
            if ok:
                try:
                    v1 = parse_values(inputs.itemById('values').value)
                    v2 = parse_values(inputs.itemById('values2').value) if name2 else None
                    build_variants(v1, v2, inputs.itemById('combine').selectedItem.name)
                except ValueError:
                    ok = False
            args.areInputsValid = bool(ok)
        except Exception:
            args.areInputsValid = False


class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = adsk.fusion.Design.cast(_app.activeProduct)
            cfg = read_inputs(args.command.commandInputs)
            save_settings(cfg)
            run_sweep(design, cfg)
        except Exception:
            _ui.messageBox('Failed:\n' + traceback.format_exc())


class DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        adsk.terminate()


# ------------------------------------------------------------- entry point

def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        design = adsk.fusion.Design.cast(_app.activeProduct)
        if not design:
            _ui.messageBox('Open a design and switch to the Design workspace first.')
            return
        if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
            _ui.messageBox('This needs a parametric design (timeline enabled).')
            return
        if design.allParameters.count == 0:
            _ui.messageBox('This design has no parameters yet.')
            return

        old = _ui.commandDefinitions.itemById(CMD_ID)
        if old:
            old.deleteMe()
        cmd_def = _ui.commandDefinitions.addButtonDefinition(
            CMD_ID, CMD_NAME, 'Export STL / 3MF / STEP for each value of one or two parameters')
        h = CreatedHandler()
        cmd_def.commandCreated.add(h)
        _handlers.append(h)
        cmd_def.execute()
        adsk.autoTerminate(False)     # keep the script alive while the dialog is open
    except Exception:
        if _ui:
            _ui.messageBox('Failed:\n' + traceback.format_exc())
