# Fusion_Parameter_Sweep

A Fusion script that generates variants of a parametric design. Pick one or two
parameters, give each a list of values, and it exports STL, 3MF and/or STEP for
every variant. When it finishes (or is cancelled, or hits an error) it puts the
parameters back to their original values.

## Install

1. Unzip so you have a folder named `ParamSweepSTL` containing
   `ParamSweepSTL.py` and `ParamSweepSTL.manifest`. Keep the folder name and the
   file names the same.
2. In Fusion: **Utilities > Scripts and Add-Ins** (Shift+S).
3. Add a script from your device and select the `ParamSweepSTL` folder.
4. Open your design, select `ParamSweepSTL` in the list and run it.

If the script does not appear in the list on an older Fusion build, open
`ParamSweepSTL.manifest` and change `"autodeskProduct": "Fusion"` to
`"Fusion360"`.

## Requirements

- A parametric design (timeline on). Direct-modeling designs have no parameters.
- At least one parameter. User parameters (Modify > Change Parameters) are the
  easiest to work with, but model parameters (d1, d2...) work too.

## The dialog

| Field | What it does |
| --- | --- |
| Parameter 1 / Values 1 | The parameter to sweep and its values. |
| Parameter 2 / Values 2 | Optional second parameter. Leave on `(none)` for a single-parameter sweep. |
| Combine | Only with two parameters. **Every combination** makes a grid (3 values x 4 values = 12 variants). **Paired** steps both lists together (1st with 1st, 2nd with 2nd) and needs lists of equal length. |
| Show model parameters | Adds d1, d2... to both parameter lists. |
| Output folder | Where files go. Created if it does not exist. |
| File name | Name template, see tokens below. |
| Export STL / 3MF / STEP | Tick any combination. At least one is required. |
| STL / 3MF contain | Whole design in one file per variant, or one file per visible solid body. |
| Mesh refinement | High, Medium or Low. Applies to STL and 3MF. |
| Binary STL | Untick for ASCII STL. |
| Skip values that break the model | If a variant puts a timeline feature into an error state, skip it and report it instead of exporting broken geometry. |

The Export button stays disabled while something is invalid: no format ticked,
the same parameter chosen twice, unparseable values, or unequal list lengths in
Paired mode.

Your settings are remembered in `last_settings.json` next to the script.

## Values syntax

Separate entries with commas. Numbers are in the parameter's own unit.

| You type | You get |
| --- | --- |
| `10, 12.5, 15` | 10, 12.5, 15 |
| `10:20:2` | 10, 12, 14, 16, 18, 20 (start:stop:step, stop included) |
| `1:0.4:0.2` | 1, 0.8, 0.6, 0.4 (descending works) |
| `5, 10:20:5, 40` | 5, 10, 15, 20, 40 (mixing is fine) |
| `25.4 mm / 2` | Passed to Fusion as a raw expression |

Raw expressions can reference other parameters, for example `width * 2`.

## File names

Default template: `{doc}_{param}_{value}`

| Token | Replaced with |
| --- | --- |
| `{doc}` | Document name, without the version suffix |
| `{param}`, `{value}` | Name and current value of parameter 1 |
| `{param2}`, `{value2}` | Name and current value of parameter 2 |
| `{index}` | Variant number, zero padded (001, 002...) |
| `{body}` | Body name, in per-body mode |

To keep files from overwriting each other, `_{param2}_{value2}` is appended
automatically when a second parameter is used and the template has no
`{value2}`, and `_{body}` is appended in per-body mode when the template has no
`{body}`.

Example: sweeping `width` over `10, 20` and `height` over `5, 8` on a document
called Bracket gives

```
Bracket_width_10_height_5.stl
Bracket_width_10_height_8.stl
Bracket_width_20_height_5.stl
Bracket_width_20_height_8.stl
```

## Format notes

- **STL and 3MF** are meshes. They follow the "contain" and "Mesh refinement"
  settings.
- **STEP** is exact geometry and is always the whole design, one file per
  variant, even in per-body mode. The Fusion API does not accept a single body
  as STEP export geometry.
- Existing files with the same name are overwritten.

## After a run

A summary box lists how many files were written, any variants that were skipped
or failed (with the reason), and confirms the restored parameter values.
Cancel in the progress box stops after the current variant.

## Troubleshooting

- **"Fusion rejected ..."**: the value is not valid for that parameter, for
  example a length given to an angle parameter, or a typo in a raw expression.
- **"feature error: ..."**: that value makes the named timeline feature fail
  (a fillet too large for the edge, a sketch that no longer solves). Untick
  "Skip values that break the model" to export anyway.
- **Large designs are slow**: each variant recomputes the timeline. A grid
  multiplies quickly, so start with a small list.
- **Parameters not restored**: this only happens if Fusion itself crashes
  mid-run. Use Undo or re-enter the value shown in Change Parameters.

## Limits

- Up to 2000 variants per run.
- Text parameters are not supported.
- Per-body mode exports visible solid bodies only. Hide what you do not want.

