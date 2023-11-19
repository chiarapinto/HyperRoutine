import ROOT
ROOT.gROOT.SetBatch(True)
ROOT.RooMsgService.instance().setSilentMode(True)
ROOT.RooMsgService.instance().setGlobalKillBelow(ROOT.RooFit.ERROR)

import numpy as np
import uproot
import argparse
import yaml
import copy
from itertools import product
from hipe4ml.tree_handler import TreeHandler

import sys
sys.path.append('utils')
import utils as utils
from spectra import SpectraMaker


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Configure the parameters of the script.')
    parser.add_argument('--config-file', dest='config_file', help="path to the YAML file with configuration.", default='')
    args = parser.parse_args()
    if args.config_file == "":
        print('** No config file provided. Exiting. **')
        exit()

    config_file = open(args.config_file, 'r')
    config = yaml.full_load(config_file)
    input_file_name_data = config['input_files_data']
    input_file_name_mc = config['input_files_mc']
    input_analysis_results_file = config['input_analysis_results_file']
    output_dir_name = config['output_dir']
    output_file_name = config['output_file']
    ct_bins = config['ct_bins']
    selections_std = config['selection']
    is_matter = config['is_matter']
    do_syst = config['do_syst']
    n_trials = config['n_trials']

    matter_options = ['matter', 'antimatter', 'both']
    if is_matter not in matter_options:
        raise ValueError(f'Invalid is-matter option. Expected one of: {matter_options}')

    print('**********************************')
    print('    Running ct_analysis.py')
    print('**********************************\n')
    print("----------------------------------")
    print("** Loading data and apply preselections **")

    data_hdl = TreeHandler(input_file_name_data, 'O2datahypcands')
    mc_hdl = TreeHandler(input_file_name_mc, 'O2mchypcands')

    lifetime_dist = ROOT.TH1D('syst_lifetime', ';#tau ps ;counts', 40, 160, 340)
    lifetime_prob = ROOT.TH1D('prob_lifetime', ';prob. ;counts', 100, 0, 1)
    
    # declare output file
    output_file = ROOT.TFile.Open(f'{output_dir_name}/{output_file_name}.root', 'recreate')

    # Add columns to the handlers
    utils.correct_and_convert_df(data_hdl, calibrate_he3_pt=True)
    utils.correct_and_convert_df(mc_hdl, calibrate_he3_pt=True, isMC=True)

    # apply preselections
    matter_sel = ''
    mc_matter_sel = ''
    if is_matter == 'matter':
        matter_sel = 'fIsMatter == True'
        mc_matter_sel = 'fGenPt > 0'

    elif is_matter == 'antimatter':
        matter_sel = 'fIsMatter == False'
        mc_matter_sel = 'fGenPt < 0'

    if matter_sel != '':
        data_hdl.apply_preselections(matter_sel)
        mc_hdl.apply_preselections(mc_matter_sel)

    # reweight MC pT spectrum
    spectra_file = ROOT.TFile.Open('utils/heliumSpectraMB.root')
    he3_spectrum = spectra_file.Get('fCombineHeliumSpecLevyFit_0-100')
    spectra_file.Close()
    utils.reweight_pt_spectrum(mc_hdl, 'fAbsGenPt', he3_spectrum)

    mc_hdl.apply_preselections('rej==True')
    mc_hdl.apply_preselections('fGenCt < 28.5 or fGenCt > 28.6') ### Needed to remove the peak at 28.5 cm in the anchored MC
    mc_reco_hdl = mc_hdl.apply_preselections('fIsReco == 1', inplace=False)

    print("** Data loaded. ** \n")
    print("----------------------------------")
    print("** Starting ct analysis **")

    output_dir_std = output_file.mkdir('std')
    spectra_maker = SpectraMaker()

    spectra_maker.data_hdl = data_hdl
    spectra_maker.mc_hdl = mc_hdl
    spectra_maker.mc_reco_hdl = mc_reco_hdl

    spectra_maker.n_ev = uproot.open(input_analysis_results_file)['hyper-reco-task']['hZvtx'].values().sum()
    spectra_maker.branching_ratio = 0.25
    spectra_maker.delta_rap = 2.0

    spectra_maker.var = 'fCt'
    spectra_maker.bins = ct_bins

    sel_string_list = [utils.convert_sel_to_string(sel) for sel in selections_std]
    spectra_maker.selection_string = sel_string_list
    spectra_maker.is_matter = is_matter

    spectra_maker.output_dir = output_dir_std

    fit_range = [ct_bins[0], ct_bins[-1]]
    spectra_maker.fit_range = fit_range

    # create raw spectra
    spectra_maker.make_spectra()
    spectra_maker.make_histos()

    # define fit function
    expo = ROOT.TF1('myexpo', '[0]*exp(-x/([1]*0.029979245800))/((exp(-[2]/([1]*0.029979245800)) - exp(-[3]/([1]*0.029979245800))) * [1]*0.029979245800)', fit_range[0], fit_range[1])
    expo.SetParLimits(1, 150, 500)
    expo.FixParameter(2, fit_range[0])
    expo.FixParameter(3, fit_range[1])
    expo.SetLineColor(ROOT.kRed)

    spectra_maker.fit_func = expo
    spectra_maker.fit_options = 'MI+'
    spectra_maker.fit()
    spectra_maker.dump_to_output_dir()
    spectra_maker.del_dyn_members()

    print("** ct analysis done. ** \n")


    if do_syst:
        n_trials = config['n_trials']
        output_dir_syst = output_file.mkdir('trials')
        ## list of trial strings to be printed to a text file
        trial_strings = []
        print("----------------------------------")
        print("** Starting systematics analysis **")
        print(f'** {n_trials} combinations will be tested **')



        cut_dict_syst = config['cut_dict_syst']
        signal_fit_func_syst = config['signal_fit_func_syst']
        bkg_fit_func_syst = config['bkg_fit_func_syst']
        ### create a dictionary with the same keys
        cut_string_dict = {}
        for var in cut_dict_syst:
            var_dict = cut_dict_syst[var]
            cut_greater = var_dict['cut_greater']
            cut_greater_string = " > " if cut_greater else " < "

            cut_list = var_dict['cut_list']
            cut_arr = np.linspace(cut_list[0], cut_list[1], cut_list[2])
            cut_string_dict[var] = []
            for cut in cut_arr:
                cut_string_dict[var].append(var + cut_greater_string + str(cut))
    

        combos = list(product(*list(cut_string_dict.values())))
        combo_random_indices = np.random.randint(len(combos), size=(n_trials, len(ct_bins) - 1))

        combo_check_map = {}

        for i_combo, combo_indices in enumerate(combo_random_indices):
            trial_strings.append("----------------------------------")
            trial_num_string = f'Trial: {i_combo} / {len(combo_random_indices)}'
            trial_strings.append(trial_num_string)
            print(trial_num_string)

            cut_selection_list = []
            bkg_fit_func_list = []
            signal_fit_func_list = []

            for ict in range(len(ct_bins) - 1):
                combo = combos[combo_indices[ict]]
                ct_bin = [ct_bins[ict], ct_bins[ict + 1]]
                sel_string = " & ".join(combo)
                full_combo_string = f'ct {ct_bin[0]}_{ct_bin[1]} | '
                full_combo_string += sel_string

                ### extract a signal and a background fit function
                signal_fit_func = signal_fit_func_syst[np.random.randint(len(signal_fit_func_syst))]
                bkg_fit_func = bkg_fit_func_syst[np.random.randint(len(bkg_fit_func_syst))]
                full_combo_string += " & " + signal_fit_func + " & " + bkg_fit_func

                if full_combo_string in combo_check_map:
                    break
                
                combo_check_map[full_combo_string] = True
                
                cut_selection_list.append(sel_string)
                bkg_fit_func_list.append(bkg_fit_func)
                signal_fit_func_list.append(signal_fit_func)
            
            if len(cut_selection_list) != len(ct_bins) - 1:
                continue

            trial_strings.append(str(cut_selection_list))
            trial_strings.append(str(bkg_fit_func_list))
            trial_strings.append(str(signal_fit_func_list))

            ### make_spectra
            spectra_maker.selection_string = cut_selection_list
            spectra_maker.inv_mass_signal_func = signal_fit_func_list
            spectra_maker.inv_mass_bkg_func = bkg_fit_func_list

            spectra_maker.make_spectra()
            spectra_maker.make_histos()

            ## prepare for exponential fit
            start_bin = spectra_maker.h_corrected_counts.FindBin(fit_range[0])
            end_bin = spectra_maker.h_corrected_counts.FindBin(fit_range[1])
            expo.FixParameter(0, spectra_maker.h_corrected_counts.Integral(start_bin, end_bin, "width"))
            spectra_maker.fit()

            res_string = "Lifetime: " + str(spectra_maker.fit_func.GetParameter(1)) + " +- " + str(spectra_maker.fit_func.GetParError(1)) + " Prob: " + str(spectra_maker.fit_func.GetProb())
            trial_strings.append(res_string)

            if spectra_maker.fit_func.GetProb() > 0.15:
                trial_dir = output_dir_syst.mkdir(f'trial_{i_combo}')
                spectra_maker.output_dir = trial_dir
                spectra_maker.dump_to_output_dir()
                lifetime_dist.Fill(spectra_maker.fit_func.GetParameter(1))
                lifetime_prob.Fill(spectra_maker.fit_func.GetProb())

            spectra_maker.del_dyn_members()
    
    
    output_dir_std.cd()
    lifetime_dist.Write()
    lifetime_prob.Write()
    output_file.Close()

    print("** Systematics analysis done. ** \n")

    ## write trial strings to a text file
    with open(f'{output_dir_name}/{output_file_name}.txt', 'w') as f:
        for trial_string in trial_strings:
            if isinstance(trial_string, list):
                for line in trial_string:
                    f.write("%s\n" % line)
            else:
                f.write("%s\n" % trial_string)