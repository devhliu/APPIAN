import nipype.pipeline.engine as pe
import nipype.interfaces.utility as niu
from nipype.interfaces.base import (TraitedSpec, File, traits, InputMultiPath, 
        BaseInterface, OutputMultiPath, BaseInterfaceInputSpec, isdefined)
from nipype.interfaces.ants import registration, segmentation
import nipype.interfaces.utility as util
import Initialization.initialization as init
import nipype.interfaces.io as nio
import os
from MRI.mincbeast import mincbeastCommand, mincbeast_library, beast_normalize_with_conversion, mincbeast, create_alt_template
from Extra.mincants import mincRegistration, mincANTSCommand, mincAtroposCommand
from Extra.conversion import mnc2niiCommand
from Extra.extra import copyCommand
import nipype.interfaces.minc as minc
from Registration.registration import PETtoT1LinRegRunning, nLinRegRunning
from nipype.interfaces.utility import Rename

def get_workflow(name, opts):
    workflow = pe.Workflow(name=name)
    in_fields = ['t1']
    if opts.user_brainmask :
        in_fields += ['brain_mask_mni']
    
    if opts.user_t1mni :
        in_fields += ['xfmT1MNI']

    label_types = [opts.tka_label_type, opts.pvc_label_type, opts.results_label_type]
    stages = ['tka', 'pvc', 'results']
    label_imgs= [opts.tka_label_img, opts.pvc_label_img, opts.results_label_img  ]

    inputnode = pe.Node(niu.IdentityInterface(fields=in_fields), name="inputnode")

    out_fields=['xfmMNIT1', 'xfmT1MNI',  'xfmT1MNI_invert',  'brain_mask_mni', 'brain_mask_t1', 't1_mni', 't1_nat' ]
    for stage, label_type in zip(stages, label_types):
        if 'internal_cls' == label_type :
            out_fields += [ stage+'_label_img']
    
    outputnode = pe.Node(niu.IdentityInterface(fields=out_fields), name='outputnode')

    #
    # Setup dir for minc beast if not using user provided brain mask
    #
    if not opts.user_brainmask : 
        if opts.beast_library_dir == None :
            library_dir = mincbeast_library(opts.template)
        else :
            library_dir = opts.beast_library_dir
        
        template_rsl = create_alt_template(opts.template, library_dir)

    ##########################################
    # T1 spatial (+ intensity) normalization #
    ##########################################
    print opts.coreg_method
    if not opts.user_t1mni:
        if opts.t1_normalize_method == 'ants' :
            mri2template = pe.Node(interface=mincRegistration(args='--float',
                collapse_output_transforms=True,
                initial_moving_transform_com=True,
                num_threads=1,
                output_inverse_warped_image=True,
                output_warped_image=True,
                sigma_units=['vox']*3,
                transforms=['Rigid', 'Affine', 'SyN'],
                terminal_output='file',
                winsorize_lower_quantile=0.005,
                winsorize_upper_quantile=0.995,
                convergence_threshold=[1e-08, 1e-08, -0.01],
                convergence_window_size=[20, 20, 5],
                metric=['Mattes', 'Mattes', ['Mattes', 'CC']],
                metric_weight=[1.0, 1.0, [0.5, 0.5]],
                #number_of_iterations=[[10000, 11110, 11110],
                #    [10000, 11110, 11110],
                #    [100, 30, 20]],
                number_of_iterations=[[1, 1, 1],
                    [1, 1, 1],
                    [1, 1, 1]],
                radius_or_number_of_bins=[32, 32, [32, 4]],
                sampling_percentage=[0.3, 0.3, [None, None]],
                sampling_strategy=['Regular',
                    'Regular',
                    [None, None]],
                shrink_factors=[[3, 2, 1],
                    [3, 2, 1],
                    [4, 2, 1]],
                smoothing_sigmas=[[4.0, 2.0, 1.0],
                    [4.0, 2.0, 1.0],
                    [1.0, 0.5, 0.0]],
                transform_parameters=[(0.1,),
                    (0.1,),
                    (0.2, 3.0, 0.0)],
                use_estimate_learning_rate_once=[True]*3,
                use_histogram_matching=[False, False, True],
                write_composite_transform=True),
                name="mincANTS_registration")

            mri2template.inputs.write_composite_transform=True
            
            mnc2nii_moving = pe.Node(mnc2niiCommand(), name="mnc2nii_moving")
            workflow.connect(inputnode, 't1', mnc2nii_moving, 'in_file')
            workflow.connect(mnc2nii_moving, 'out_file', mri2template, 'moving_image')
           
            mnc2nii_fixed = pe.Node(mnc2niiCommand(), name="mnc2nii_fixed")
            mnc2nii_fixed.inputs.in_file = fixed_image=opts.template
            workflow.connect(mnc2nii_fixed, 'out_file', mri2template, 'fixed_image')

            t1_mni_file = 'warped_image'
            t1_mni_node=mri2template
            tfm_node= mri2template
            tfm_file='composite_transform'
        elif opts.coreg_method == 'minctracc' :
            mri2template = pe.Node(interface=nLinRegRunning(), name="mri_normalize")
            mri2template.inputs.in_target_file=opts.template
            workflow.connect(inputnode, 't1', mri2template, 'in_source_file')
            t1_mni_file = 'out_file_img'
            t1_mni_node=mri2template
            tfm_node= mri2template
            tfm_file='out_file_xfm'
        else :
            mri2template = pe.Node(interface=beast_normalize_with_conversion(), name="mri_normalize")
            template_name = os.path.splitext(os.path.basename(template_rsl))[0]
            template_dir = os.path.dirname(opts.template)
            mri2template.inputs.modelname = template_name
            mri2template.inputs.modeldir = template_dir
            workflow.connect(inputnode, 't1', mri2template, 'in_file')

            t1_mni_file = 'out_file_vol'
            t1_mni_node=mri2template
            tfm_node= mri2template
            tfm_file='out_file_xfm'
    else :
        transform_t1 = pe.Node(interface=minc.Resample(), name="transform_t1"  )
        transform_t1.inputs.two=True
        workflow.connect(inputnode, 't1', transform_t1, 'input_file')
        workflow.connect(inputnode, 'xfmT1MNI', transform_t1, 'transformation')
        transform_t1.inputs.like = opts.template

        t1_mni_node = transform_t1
        t1_mni_file = 'output_file'
        tfm_node = inputnode
        tfm_file = 'xfmT1MNI'

    #
    # Invert transformation from T1 to MNI space
    #
    xfmMNIT1 = pe.Node(interface=minc.XfmInvert(), name="MNIT1")
    workflow.connect(tfm_node, tfm_file, xfmMNIT1 , 'input_file')

    #
    # T1 in native space will be part of the APPIAN target directory
    # and hence it won't be necessary to link to the T1 in the source directory.
    #
    copy_t1_nat = pe.Node(interface=copyCommand(), name="t1_nat"  )
    workflow.connect(inputnode, 't1', copy_t1_nat, 'input_file')

    ####################
    # T1 Brain masking #
    ####################
    if not opts.user_brainmask :
        #Brain Mask MNI-Space
        t1MNI_brain_mask = pe.Node(interface=mincbeast(), name="t1_mni_brain_mask")
        t1MNI_brain_mask.inputs.library_dir  = library_dir
        t1MNI_brain_mask.inputs.configuration = t1MNI_brain_mask.inputs.library_dir+os.sep+"default.2mm.conf"
        t1MNI_brain_mask.inputs.same_resolution = True
        t1MNI_brain_mask.inputs.median = True
        t1MNI_brain_mask.inputs.fill = True
        #t1MNI_brain_mask.inputs.voxel_size=opts.beast_voxel_size
        t1MNI_brain_mask.inputs.median = opts.beast_median

        workflow.connect(t1_mni_node, t1_mni_file, t1MNI_brain_mask, "in_file" )

        brain_mask_node = t1MNI_brain_mask
        brain_mask_file = 'out_file'
    else :			
        brain_mask_node = inputnode
        brain_mask_file = 'brain_mask_mni'
    #
    # Transform brain mask from stereotaxic to T1 native space
    #
    transform_brain_mask = pe.Node(interface=minc.Resample(), name="transform_brain_mask"  )
    transform_brain_mask.inputs.nearest_neighbour_interpolation = True
    transform_brain_mask.inputs.invert_transformation = True
    workflow.connect(brain_mask_node, brain_mask_file, transform_brain_mask, 'input_file')
    workflow.connect(inputnode, 't1', transform_brain_mask, 'like')
    workflow.connect(tfm_node, tfm_file, transform_brain_mask, 'transformation')


    ###################################
    # Segment T1 in Stereotaxic space #
    ###################################
    seg=None
    for stage, label_type, img in zip(stages, label_types, label_imgs) :
        if 'antsAtropos' == img and seg == None :
            seg = pe.Node(interface=mincAtroposCommand(), name="segmentation_ants")
            seg.inputs.dimension=3
            seg.inputs.number_of_tissue_classes=3 #... opts.
            seg.inputs.initialization = 'Otsu'
            workflow.connect(t1_mni_node, t1_mni_file,  seg, 'intensity_images' )
            workflow.connect(brain_mask_node, brain_mask_file,  seg, 'mask_image' )
        print(stage, img) 
        if 'antsAtropos' == img :
           workflow.connect(seg, 'classified_image', outputnode, stage+'_label_img')
    
    ###############################
    # Pass results to output node #
    ###############################
    workflow.connect(brain_mask_node, brain_mask_file, outputnode, 'brain_mask_mni')
    workflow.connect(tfm_node, tfm_file, outputnode, 'xfmT1MNI' )
    workflow.connect(xfmMNIT1, 'output_file', outputnode, 'xfmMNIT1' )
    workflow.connect(transform_brain_mask, 'output_file', outputnode, 'brain_mask_t1')
    workflow.connect(t1_mni_node, t1_mni_file, outputnode, 't1_mni')
    workflow.connect(copy_t1_nat, 'output_file', outputnode, 't1_nat')
    return(workflow)


