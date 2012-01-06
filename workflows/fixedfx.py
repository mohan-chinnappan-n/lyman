from nipype.interfaces import fsl
from nipype.interfaces import freesurfer as surf
from nipype.interfaces.utility import IdentityInterface, Function
from nipype.pipeline.engine import Workflow, Node

from .model import plot_zstats


def create_volume_ffx_workflow(name="volume_ffx"):

    inputnode = Node(IdentityInterface(fields=["subject_id",
                                               "contrast",
                                               "copes",
                                               "varcopes",
                                               "masks",
                                               "dof_files",
                                               "background_file"]),
                        name="inputnode")

    # Concatenate the inputs for each run
    copemerge = Node(fsl.Merge(dimension="t"),
                     name="copemerge")

    varcopemerge = Node(fsl.Merge(dimension="t"),
                        name="varcopemerge")

    # Create suitable DOF images to use in FLAME
    createdof = Node(Function(input_names=["copes",
                                           "dof_files"],
                              output_names=["out_file"],
                              function=create_dof_image),
                     name="createdof")

    # Determine a mask by area of overlap of nonzero varcopes
    getmask = Node(Function(input_names=["masks",
                                         "background_file"],
                            output_names=["mask_file",
                                          "mask_png"],
                            function=create_ffx_mask),
                   name="getmask")

    # Set up a fixed effects FLAMEO model
    ffxmodel = Node(fsl.L2Model(),
                    name="ffxmodel")

    # Run a fixed effects analysis in FLAMEO
    flameo = Node(fsl.FLAMEO(run_mode="fe"),
                  name="flameo")

    # Plot the zstat images
    plotzstats = Node(Function(input_names=["background_file",
                                            "zstat_files",
                                            "contrasts"],
                               output_names=["out_files"],
                               function=plot_zstats),
                         name="plotzstats")
    plotzstats.inputs.contrasts = ["main_effect"]

    # Build pdf and html reports
    report = Node(Function(input_names=["subject_id",
                                        "mask_png",
                                        "zstat_pngs",
                                        "contrast"],
                           output_names=["reports"],
                           function=write_ffx_report),
                  name="report")

    outputnode = Node(IdentityInterface(fields=["stats",
                                                "zstat_png",
                                                "mask_png",
                                                "report"]),
                      name="outputnode")

    ffx = Workflow(name=name)
    ffx.connect([
        (inputnode, copemerge,
            [("copes", "in_files")]),
        (inputnode, varcopemerge,
            [("varcopes", "in_files")]),
        (inputnode, createdof,
            [("copes", "copes"),
             ("dof_files", "dof_files")]),
        (copemerge, flameo,
            [("merged_file", "cope_file")]),
        (varcopemerge, flameo,
            [("merged_file", "var_cope_file")]),
        (createdof, flameo,
            [("out_file", "dof_var_cope_file")]),
        (inputnode, getmask,
            [("masks", "masks"),
             ("background_file", "background_file")]),
        (getmask, flameo,
            [("mask_file", "mask_file")]),
        (inputnode, ffxmodel,
            [(("copes", length), "num_copes")]),
        (ffxmodel, flameo,
            [("design_mat", "design_file"),
             ("design_con", "t_con_file"),
             ("design_grp", "cov_split_file")]),
        (flameo, plotzstats,
            [("zstats", "zstat_files")]),
        (inputnode, plotzstats,
            [("background_file", "background_file")]),
        (inputnode, report,
            [("subject_id", "subject_id"),
             ("contrast", "contrast")]),
        (plotzstats, report,
            [("out_files", "zstat_pngs")]),
        (getmask, report,
            [("mask_png", "mask_png")]),
        (flameo, outputnode,
            [("stats_dir", "stats")]),
        (getmask, outputnode,
            [("mask_png", "mask_png")]),
        (plotzstats, outputnode,
            [("out_files", "zstat_png")]),
        (report, outputnode,
            [("reports", "report")]),
        ])

    return ffx, inputnode, outputnode


def create_surface_ffx_workflow(name="surface_ffx"):

    pass


def create_dof_image(copes, dof_files):
    """Create images where voxel values are DOF for that run."""
    from os.path import abspath
    from numpy import ones, int16
    from nibabel import load, Nifti1Image
    cope_imgs = map(load, copes)
    data_shape = list(cope_imgs[0].shape) + [len(cope_imgs)]
    dof_data = ones(data_shape, int16)
    for i, img in enumerate(cope_imgs):
        dof_val = int(open(dof_files[i]).read().strip())
        dof_data[..., i] *= dof_val

    template_img = load(copes[0])
    dof_hdr = template_img.get_header()
    dof_hdr.set_data_dtype(int16)
    dof_img = Nifti1Image(dof_data,
                          template_img.get_affine(),
                          dof_hdr)
    out_file = abspath("dof.nii.gz")
    dof_img.to_filename(out_file)
    return out_file


def create_ffx_mask(masks, background_file):
    """Create a mask for areas that are nonzero in all masks"""
    import os
    from os.path import abspath, join
    from nibabel import load, Nifti1Image
    from numpy import zeros
    from subprocess import call
    mask_imgs = map(load, masks)
    data_shape = list(mask_imgs[0].shape) + [len(mask_imgs)]
    mask_data = zeros(data_shape, int)
    for i, img in enumerate(mask_imgs):
        data = img.get_data()
        data[data < 1e-4] = 0
        mask_data[..., i] = img.get_data()

    out_mask_data = mask_data.all(axis=-1)
    sum_data = mask_data.astype(bool).sum(axis=-1)

    template_img = load(masks[0])
    template_affine = template_img.get_affine()
    template_hdr = template_img.get_header()

    mask_img = Nifti1Image(out_mask_data, template_affine, template_hdr)
    mask_file = abspath("fixed_effects_mask.nii.gz")
    mask_img.to_filename(mask_file)

    sum_img = Nifti1Image(sum_data, template_affine, template_hdr)
    sum_file = abspath("mask_overlap.nii.gz")
    sum_img.to_filename(sum_file)

    overlay_file = abspath("mask_overlap_overlay.nii.gz")
    call(["overlay", "1", "0", background_file, "-a",
          sum_file, "1", str(len(masks)), overlay_file])
    native = sum_img.shape[-1] < 50
    width = 750 if native else 872
    sample = 1 if native else 2
    mask_png = abspath("mask_overlap.png")
    lut = join(os.environ["FSLDIR"], "etc/luts/renderjet.lut")
    call(["slicer", overlay_file, "-l", lut, "-S",
          str(sample), str(width), mask_png])

    return mask_file, mask_png


def write_ffx_report(subject_id, mask_png, zstat_pngs, contrast):
    import os.path as op
    import time
    from subprocess import call
    from workflows.reporting import ffx_report_template

    # Fill in the report template dict
    report_dict = dict(now=time.asctime(),
                       subject_id=subject_id,
                       contrast_name=contrast,
                       mask_png=mask_png,
                       zstat_png=zstat_pngs[0])

    # Plug the values into the template for the pdf file
    report_rst_text = ffx_report_template % report_dict

    # Write the rst file and convert to pdf
    report_pdf_rst_file = "ffx_report.rst"
    report_pdf_file = op.abspath("ffx_report.pdf")
    open(report_pdf_rst_file, "w").write(report_rst_text)
    call(["rst2pdf", report_pdf_rst_file, "-o", report_pdf_file])

    # For images going into the html report, we want the path to be relative
    # (We expect to read the html page from within the datasink directory
    # containing the images.  So iteratate through and chop off leading path
    # of anyhting ending in .png
    for k, v in report_dict.items():
        if v.endswith(".png"):
            report_dict[k] = op.basename(v)

    # Write the another rst file and convert it to html
    report_html_rst_file = "ffx_html.rst"
    report_html_file = op.abspath("ffx_report.html")
    report_rst_text = ffx_report_template % report_dict
    open(report_html_rst_file, "w").write(report_rst_text)
    call(["rst2html.py", report_html_rst_file, report_html_file])

    # Return both report files as a list
    reports = [report_pdf_file, report_html_file]
    return reports


def length(x):
    return len(x)