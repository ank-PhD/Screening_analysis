import os
import numpy as np
from collections import defaultdict
from csv import reader, writer
from matplotlib import pyplot as plt
from scipy.optimize import minimize
from pickle import dump, load
import pandas as pd
import seaborn.apionly as sns
from chiffatools.linalg_routines import rm_nans
import matplotlib as mlb

mlb.rcParams['font.size'] = 10.0
mlb.rcParams['figure.figsize'] = (25, 15)


debug = False
aspects = {
        'debug':['flatten_and_group.pre_process']
        }

source_folder = 'L:\\Users\\andrei\\real-runs\\'

past_mappings = os.path.join(source_folder, 'conditions-to-include-in-clustering_2010-09-16.csv') # used by Pavelka in his clustering
relative_growth = os.path.join(source_folder, 'meanNormalizedFinalAverageSpotIntensity.csv')
spot_locations = os.path.join(source_folder, 'spotLocation.csv')
output_location = os.path.join(source_folder, 'Re_analysis_ank-2.csv')
total_log = os.path.join(source_folder, 'Re_analysis_ank-failed_to_parse.csv')
output_folder = os.path.join(source_folder, 'Re_analysis_ank-2')


def growth(timepoints, stepness, maxVal, midpoint, delay):
    # maxVal = 50  # works well
    # we will just remember all the parameters; this functions seems to be working better for some reason then the other ones.
    # if delay > 0: # delay control
    preliminary = maxVal/(1. + np.exp(-np.log(2)*stepness*(timepoints - midpoint)))
    # preliminary[timepoints < delay] = 0
    return preliminary


def minimize_function_builder(timepoints, means, errors):

    def minfunct(paramset):
        stepness, maxVal, midpoint, delay = paramset
        estimate = growth(timepoints, stepness, maxVal, midpoint, delay)
        difference = np.abs(estimate - means)
        # ponderated_difference = difference/np.abs(errors)
        ponderated_difference = difference
        return np.sum(ponderated_difference**2)

    return minfunct


def minimize_function_builder2(timepoints, means):

    def minfunct(paramset):
        stepness, maxVal, midpoint, delay = paramset
        estimate = growth(timepoints, stepness, maxVal, midpoint, delay)
        difference = np.abs(estimate - means)
        return np.sum(difference**2)

    return minfunct


def rec_path_join(base, *args):
    for elt in args:
        base = os.path.join(base, elt)
    return base


def build_unified_mappings(mappings_file):
    storage_dict = defaultdict(list)
    authorised_replicates = {}
    with open(mappings_file, 'rb') as source:
        csv_reader = reader(source)
        csv_reader.next()
        for line in csv_reader:
            if line:
                standard_name = line[0]
                standard_folder = rec_path_join(source_folder, line[1], line[2])
                storage_dict[standard_name].append(standard_folder)
                authorised_replicates[standard_folder] = line[3].split(';')

    storage_dict = dict([(key, tuple(value)) for key, value in storage_dict.iteritems()])
    return storage_dict, authorised_replicates


def check_unified_mappings(unified_mapping, authorised_replicas):
    for name, location_list in unified_mapping.iteritems():
        for location in location_list:
            if not os.path.isdir(location):
                print 'location %s for %s is mapped wrong' % (location, name)


# read location-depended spot locations:
def build_spot_map():
    spot_dict = defaultdict(dict)
    with open(spot_locations, 'rb') as source:
        csv_reader = reader(source)
        for line in csv_reader:
            if line[3]:
                spot_dict[line[1]].update({line[2]: line[3]})
    return spot_dict


def read_file(file_path, spots_map):
    with open(file_path, 'rb') as source:
        csv_reader = reader(source)
        names =[]
        times = np.array(csv_reader.next()[1:])
        table = []
        for line in csv_reader:
            if line[0] in spots_map.keys():
                names.append(spots_map[line[0]])
                table.append(line[1:])

        return np.array(names), times.astype(np.float), np.array(table).astype(np.float)


# walks the source folder and reads the original data.
def pull_curves(name2folder, folder2replicas, spots_map):
    name2full_location = defaultdict(list)
    for name, folders in name2folder.iteritems():
        for folder in folders:
            for subfolder in os.walk(folder):
                parsed_subfolder = subfolder[0].split('\\')
                if parsed_subfolder[-1][-2:] in ['_A', '_B', '_C'] \
                        and os.path.isdir(subfolder[0]):
                    for fle in subfolder[2]:
                        if 'averageSpotIntensity' in fle and parsed_subfolder[-1][-1] in folder2replicas[folder]:
                            name2full_location[name].append((parsed_subfolder[-1][-1], os.path.join(subfolder[0], fle)))

    read_out_map = defaultdict(list)
    # dico[condition] = [replica_1, replica_2, ....]
    # where replica_x = (aneuploid indexes, time_index, data_matrix)
    for name, fle_list in name2full_location.iteritems():
        for replicate, fle_to_read in fle_list:
            current_map = spots_map[replicate]
            current_dataframe = read_file(fle_to_read, current_map)
            read_out_map[name].append(current_dataframe)

    return read_out_map


def merge_replicates(condition_specific_replicas):
    aneuploid2MergedTable = defaultdict(lambda : [[], []])

    for replicate in condition_specific_replicas:
        for i, aneuploid_ID in enumerate(replicate[0]):
            time = tuple(replicate[1].tolist())
            value = replicate[2][i, :].tolist()
            if not aneuploid2MergedTable[aneuploid_ID][0]:
                aneuploid2MergedTable[aneuploid_ID][0] = time
            if time == aneuploid2MergedTable[aneuploid_ID][0]:
                aneuploid2MergedTable[aneuploid_ID][1].append(value)

    return aneuploid2MergedTable


def flatten_and_group2(condition_specific_replica, condition_name, aneuploid_index):

    def listicise(def_integer):
        return [[] for _ in range(0, def_integer)]

    def nan_diff(val1, val2):
        if val1 in ['NA', 'inf'] or val2 in ['NA', 'inf']:
            return 'NA'
        else:
            return val1-3*val2

    def iterative_fit(time, value_set):

        def gu(min, max):
            return np.random.uniform(min, max)

        v_set = np.array(value_set)
        v_set -= np.min(v_set)

        bounds = [(0.01, 0.9), (10, 150), (0, 200), (0, 1)] #TDOO: growth-wise lag optimisaiton is skrewed

        ffit, errcode = fit_with_flat(time, v_set, bounds=bounds)
        if ffit[-1] > 3 and errcode != 1:
            for i in range(0, 5):
                start = [gu(*bound) for bound in bounds]
                ffit, errcode = fit_with_flat(time, v_set, start_point=start, bounds=bounds)
                if ffit[-1] < 1:
                    break
            if ffit[-1] > 3:
                errcode = 2

        return v_set, ffit, errcode

    def fit_with_flat(time, v_set, start_point=[0.16, 50., 60., 5.], bounds=[(0.05, 0.5), (10, 150), (0, 200), (0, 10)] ):

        take_off = np.max(v_set[1:-1])
        if take_off < 10:
            return ['inf', 'NA', 'NA', 'NA', np.mean(np.abs(np.mean(v_set, axis=0)))], 1

        mfunct = minimize_function_builder2(np.array(time), v_set)
        OR_object = minimize(mfunct, start_point, method='L-BFGS-B', bounds=bounds)
        popt = OR_object.x
        if OR_object.success:
            return [1./popt[0]] + popt[1:].tolist() + [np.mean(np.abs(growth(np.array(time), *popt)-v_set))], 0

        else:
            print OR_object.message,
            print OR_object.x
            popt = OR_object.x
            return [1./popt[0]] + popt[1:].tolist() + [np.mean(np.abs(growth(np.array(time), *popt)-v_set))], -1

    def show(time, value, fit_params, name):
        plt.title(name)
        time = np.array(time)
        higher_time = np.linspace(np.min(time), np.max(time), 100)
        plt.plot(time, value, 'r')
        plt.plot(higher_time, growth(higher_time, 1/fit_params[0], *fit_params[1:-1]), 'k', label=' doubling time: %.2f h\n max: %.2f \n midpoint: %.0f h\n lag: %.0f h\n error: %.2f\n '% tuple(fit_params))
        plt.legend(loc='upper left', prop={'size':10})
        plt.show()


    aneuploid2MergedTable = merge_replicates(condition_specific_replica)

    supercollector = []
    fail_collector = []
    base_shape = len(aneuploid_index.keys())
    doubling_time_lane = listicise(base_shape)
    midpoints_lane = listicise(base_shape)
    delay_lane = listicise(base_shape)

    for aneuploid_ID, (time, value_list) in aneuploid2MergedTable.iteritems():
        doubling_time_holder = []
        midpoint_holder = []
        delay_lane_holder = []
        for repeat in value_list:
            norm_repeat, fit_params, error_code = iterative_fit(time, repeat)
            # if error_code == 0:
            #     show(time, norm_repeat, fit_params, aneuploid_ID+', '+condition_name)
            # if error_code == 2 or error_code == -1:
            #     show(time, norm_repeat, fit_params, aneuploid_ID+', '+condition_name)
            fail_collector.append([aneuploid_ID, condition_name, error_code] + repeat)
            supercollector.append([aneuploid_ID, condition_name, error_code] + fit_params)
            a_i = aneuploid_index[aneuploid_ID]
            doubling_time_holder.append(fit_params[0])
            midpoint_holder.append(fit_params[2])
            delay_lane_holder.append(nan_diff(fit_params[2], fit_params[0]))

        doubling_time_lane[a_i] = doubling_time_holder
        midpoints_lane[a_i] = midpoint_holder
        delay_lane[a_i] = delay_lane_holder

    return supercollector, fail_collector, doubling_time_lane, midpoints_lane, delay_lane


def iterate_through_conditions(readout_map):
    super_collector = [['strain', 'condition', 'fitting result', 'doubling time(h)', 'maxVal', 'midpoint', 'delay',  'fit error']]
    fail_collector = []

    condition_names = []
    speeds = []
    midpoints = []
    delays = []

    aneups_set = set()
    for _, condition_specific_replicas in readout_map.iteritems():
        for replica in condition_specific_replicas:
            aneups_set.update(set(replica[0]))

    aneup_names = sorted(list(aneups_set))
    aneup_dict = dict([(aneup_name, _i) for _i, aneup_name in enumerate(aneup_names)])

    for condition, condition_specific_replicas in readout_map.iteritems():
        condition_names += [condition]
        d_super, d_fail, doubling_time_lane, midpoints_lane, delay_lane = flatten_and_group2(condition_specific_replicas, condition, aneuploid_index=aneup_dict)
        super_collector += d_super
        fail_collector += d_fail
        speeds.append(doubling_time_lane)
        midpoints.append(midpoints_lane)
        delays.append(delay_lane)

    cons_obj = (aneup_names, condition_names, speeds, midpoints, delays)
    return super_collector, fail_collector, cons_obj


# finally, write out the resulting curves to a destination file
def write_out_curves(locations, out_path):
    with open(out_path, 'wb') as source:
        csv_writer = writer(source)
        for line in locations:
            csv_writer.writerow(line)


def gini_coeff(x):
    """
    requires all values in x to be zero or positive numbers,
    otherwise results are undefined
    source : http://www.ellipsix.net/blog/2012/11/the-gini-coefficient-for-distribution-inequality.html
    """
    x = rm_nans(x.astype(np.float))
    n = len(x)
    s = x.sum()
    r = np.argsort(np.argsort(-x)) # calculates zero-based ranks
    return 1 - (2.0 * (r*x).sum() + s)/(n*s)


def reduce_table(conservation_object):

    def reduction_routine(list_to_reduce):
        list_to_reduce = [str(elt) for elt in list_to_reduce]
        num_list = np.genfromtxt(np.array(list_to_reduce))
        non_numerical = np.logical_or(np.isnan(num_list), np.logical_not(np.isfinite(num_list)))
        if sum(non_numerical) == len(list_to_reduce):
            return np.inf, np.nan
        if sum(non_numerical) == len(list_to_reduce)-1:
            return np.nan, np.nan
        numerical_redux = num_list[np.logical_not(non_numerical)]
        mn, sd = (np.mean(numerical_redux), 1.96*np.std(numerical_redux, ddof=1)/np.sqrt(len(list_to_reduce)-sum(non_numerical)))
        if sd/mn < 0.5 and mn > 2:
            return mn, sd
        else:
            return np.nan, np.nan

    def higher_reduction(embedded_list):
        return np.array([[reduction_routine(lst) for lst in cond_lst] for cond_lst in embedded_list])

    def split_pandas_frame(data):
        df_v = pd.DataFrame(data[:, :, 0].T, aneup_names, condition_names)
        df_err = pd.DataFrame(data[:, :, 1].T, aneup_names, condition_names)
        return df_v, df_err

    def render(variable1, variable2, name):
        plt.subplot(1, 2, 1)
        plt.title(name+' values')
        sns.heatmap(variable1)
        plt.subplot(1, 2, 2)
        plt.title(name+' errors')
        sns.heatmap(variable2)
        plt.show()

    def errplot_with_selectors(table, errtable):
        for i in range(0, len(selector)):
            v1 = table.reset_index().values[:, 1:][i, :].flatten()
            v2 = errtable.reset_index().values[:, 1:][i, :].flatten()
            v1 = v1.astype(np.float)
            nl = np.sum(np.logical_not(np.isnan(v1)))
            gni = gini_coeff(1./rm_nans(v1))
            plt.errorbar(condition_index, v1, v2, fmt='.', label='%s; gini: %.2f, valid: %s'%(pre_selector[i], gni, nl))
        plt.xticks(condition_index, condition_names, rotation='vertical')
        plt.gca().set_yscale("log", nonposy='clip')
        plt.legend(loc='upper left', prop={'size':10})
        plt.show()


    def ratio_with_errs(v1, err1, v2, err2):
        verification_array = np.array((v1, v2, err1, err2))
        if np.all(np.logical_and(np.logical_not(np.isnan(verification_array)), np.isfinite(verification_array))):
            return v2/v1, np.sqrt((v2/v1)**2*(err1**2/v1**2+err2**2/v2**2))  # ATTENTION: since we are interested in
            # growth speed ratios, we are using an inverted ratio. Thus v_ref/v_calc is perfectly normal
        else:
            return np.nan, np.nan

    def ratio_wrapper(v1_vect, err1_vect, v2_vect, err2_vect):
        return [ratio_with_errs(v1_vect[_i], err1_vect[_i], v2_vect[_i], err2_vect[_i]) for _i in range(0, len(v1_vect))]


    def diff_with_errs(v1, err1, v2, err2):
        verification_array = np.array((v1, v2, err1, err2))
        if np.all(np.logical_and(np.logical_not(np.isnan(verification_array)), np.isfinite(verification_array))):
            return v1-v2, np.sqrt(err1**2+err2**2)
        else:
            return np.nan, np.nan


    def diff_wrapper(v1_vect, err1_vect, v2_vect, err2_vect):
        return [diff_with_errs(v1_vect[_i], err1_vect[_i], v2_vect[_i], err2_vect[_i]) for _i in range(0, len(v1_vect))]


    def spin_conditions():
        for condition in condition_names:
            lag = np.array(lag_v.loc[:, condition]).astype(np.float).flatten()
            lag_e = np.array(lag_errs.loc[:, condition]).astype(np.float).flatten()
            ratio = np.array(twister_v.loc[:, condition]).astype(np.float).flatten()
            ratio_e = np.array(twister_errs.loc[:, condition]).astype(np.float).flatten()
            plt.title(condition)
            for _j, a_name in enumerate(aneup_names):
                if np.isnan(lag[_j]) or not np.isfinite(lag[_j]):
                    plt.errorbar(lag[_j], ratio[_j], ratio_e[_j], lag_e[_j], fmt='.w', label=a_name)
                else:
                    plt.annotate(a_name, (lag[_j], ratio[_j]))
                    if np.abs(lag[_j]-1) < 5 and np.abs(ratio[_j]-1) < 0.1:
                        plt.errorbar(lag[_j], ratio[_j], ratio_e[_j], lag_e[_j], fmt='.k', label=a_name)
                    elif lag[_j]>10 and ratio[_j] > 1:
                        plt.errorbar(lag[_j], ratio[_j], ratio_e[_j], lag_e[_j], fmt='.r', label=a_name)
                    else:
                        plt.errorbar(lag[_j], ratio[_j], ratio_e[_j], lag_e[_j], fmt='.b', label=a_name)
            plt.legend(loc='upper left', prop={'size':10})
            plt.savefig('%s.png'%condition)
            # plt.show()
            plt.clf()

    aneup_names, condition_names, speeds, midpoints, delays = conservation_object
    speeds_v, speeds_err = split_pandas_frame(higher_reduction(speeds))
    midpoints_v, midpoints_err = split_pandas_frame(higher_reduction(midpoints))
    delays_v, delays_err = split_pandas_frame(higher_reduction(delays))

    # speeds_v.to_csv('speeds_v.csv')
    # speeds_err.to_csv('speeds_err.csv')
    # midpoints_v.to_csv('midpoints_v.csv')
    # midpoints_err.to_csv('midpoints_err.csv')
    # delays_v.to_csv('delays_v.csv')
    # delays_err.to_csv('delays_err.csv')

    re_index = dict([(name, _i) for _i, name in enumerate(aneup_names)])

    # print aneup_names

    pre_selector = ['U1', 'U2', 'U3']

    pre_selector = aneup_names

    selector = np.array([re_index[pre_s] for pre_s in pre_selector])
    condition_index = np.array([(i) for i, _ in enumerate(condition_names)])
    # bigger_index = np.repeat(condition_index, len(selector))
    # v1 = speeds_v.reset_index().values[:, 1:][selector, :].flatten()
    # v2 = speeds_err.reset_index().values[:, 1:][selector, :].flatten()

    # errplot_with_selectors(speeds_v, speeds_err)
    # errplot_with_selectors(delays_v, delays_err)

    s_v = speeds_v.reset_index().values[:, 1:]
    s_err = speeds_err.reset_index().values[:, 1:]

    ref_v = s_v [re_index['U1'], :]
    ref_err = s_err [re_index['U1'], :]

    twister_fused = np.array([ratio_wrapper(svs, serrs, ref_v, ref_err) for svs, serrs in zip(s_v.tolist(), s_err.tolist())])
    twister_fused = np.rollaxis(twister_fused, 1)
    twister_v, twister_errs = split_pandas_frame(twister_fused)


    l_v = delays_v.reset_index().values[:, 1:]
    l_err = delays_err.reset_index().values[:, 1:]

    ref_v = l_v [re_index['U1'], :]
    ref_err = l_err [re_index['U1'], :]

    lag_fused = np.array([diff_wrapper(lvs, lerrs, ref_v, ref_err) for lvs, lerrs in zip(l_v.tolist(), l_err.tolist())])
    lag_fused = np.rollaxis(lag_fused, 1)
    lag_v, lag_errs = split_pandas_frame(lag_fused)

    print lag_v

    print lag_errs

    # TODO: recalculate the euploid from the aneuploids

    spin_conditions()

    # errplot_with_selectors(twister_v, twister_errs)

    # find the one we actually want by a 2_d plot

    # ix_1 = re_index['controlHaploid']
    # ix_2 = re_index['U1']
    #
    # print np.array(twister_errs.iloc[[ix_2]]).astype(np.float)
    #
    # plt.errorbar(np.array(twister_v.iloc[[ix_1]]).astype(np.float).flatten(),
    #              np.array(twister_v.iloc[[ix_2]]).astype(np.float).flatten(),
    #              xerr=np.array(twister_errs.iloc[[ix_1]]).astype(np.float).flatten(),
    #              yerr=np.array(twister_errs.iloc[[ix_2]]).astype(np.float).flatten())
    # plt.show()



    twister_v.to_csv('twister_v.csv')
    twister_errs.to_csv('twister_errs.csv')
    #
    (twister_v-twister_errs).to_csv('twister_v_conservative.csv')

    # render(speeds_v, speeds_err, 'duplication_time')
    # render(midpoints_v, midpoints_err, 'midpoints')
    # render(delays_v, delays_err, 'delays')
    # render(np.log10(twister_v), twister_errs, 'relative_growth_speed')
    # render(lag_v, lag_errs, 'relative lags')


def regress_euploid(conservation_object):

    def reduction_routine(list_to_reduce):
        list_to_reduce = [str(elt) for elt in list_to_reduce]
        num_list = np.genfromtxt(np.array(list_to_reduce))
        non_numerical = np.logical_or(np.isnan(num_list), np.logical_not(np.isfinite(num_list)))
        if sum(non_numerical) == len(list_to_reduce):
            return 0, np.nan
        if sum(non_numerical) == len(list_to_reduce)-1:
            return np.nan, np.nan
        numerical_redux = 1. / num_list[np.logical_not(non_numerical)]
        mn, sd = (np.mean(numerical_redux), 1.96*np.std(numerical_redux, ddof=1)/np.sqrt(len(list_to_reduce)-sum(non_numerical)))
        if np.log10(mn) > np.log10(sd) and mn < 0.5:
            return mn, sd
        else:
            return np.nan, np.nan

    def errplot_with_selectors(table, errtable):
        for i in range(0, len(selector)):
            v1 = table.reset_index().values[:, 1:][selector[i], :].flatten()
            v2 = errtable.reset_index().values[:, 1:][selector[i], :].flatten()
            v1 = v1.astype(np.float)
            nl = np.sum(np.logical_not(np.isnan(v1)))
            gni = gini_coeff(v1)
            plt.errorbar(condition_index, v1, v2, fmt='.', label='%s; gini: %.2f, valid: %s'%(pre_selector[i], gni, nl))
        plt.xticks(condition_index, condition_names, rotation='vertical')
        plt.gca().set_yscale("log", nonposy='clip')
        plt.legend(loc='upper left', prop={'size':10})
        plt.show()

    def higher_reduction(embedded_list):
        return np.array([[reduction_routine(lst) for lst in cond_lst] for cond_lst in embedded_list])

    def split_pandas_frame(data):
        df_v = pd.DataFrame(data[:, :, 0].T, aneup_names, condition_names)
        df_err = pd.DataFrame(data[:, :, 1].T, aneup_names, condition_names)
        return df_v, df_err


    aneup_names, condition_names, speeds, midpoints, delays = conservation_object
    speeds_v, speeds_err = split_pandas_frame(higher_reduction(speeds))
    midpoints_v, midpoints_err = split_pandas_frame(higher_reduction(midpoints))
    delays_v, delays_err = split_pandas_frame(higher_reduction(delays))

    re_index = dict([(name, _i) for _i, name in enumerate(aneup_names)])

    # pre_selector = ['U1', 'U2', 'U3']
    pre_selector = aneup_names[:-7]
    selector = np.array([re_index[pre_s] for pre_s in pre_selector])
    condition_index = np.array([(i) for i, _ in enumerate(condition_names)])

    current_table = speeds_v.reset_index().values[:, 1:][selector , :].astype(np.float)
    current_table_err = speeds_err.reset_index().values[:, 1:][selector , :].astype(np.float)
    aneuploid_gini_indexes = np.apply_along_axis(gini_coeff, 1, current_table)
    aneuploid_mean_survival = np.apply_along_axis(np.nanmean, 1, current_table)

    print aneuploid_mean_survival

    argsort_indexes = np.argsort(aneuploid_gini_indexes)

    print aneuploid_gini_indexes[argsort_indexes]

    pre_selector = ['U1']
    selector = np.array([re_index[pre_s] for pre_s in pre_selector])
    l_idx = selector[-1]

    print np.nanmax(current_table)

    for j, i in enumerate(range(4, 7)):
        euploid_reconstruction = np.apply_along_axis(np.nanmean, 0, current_table[argsort_indexes[:i], :])
        euploid_reconstruction = euploid_reconstruction / np.nanmean(euploid_reconstruction) * np.nanmax(aneuploid_mean_survival)+0.001
        euploid_err_reconstruction = np.apply_along_axis(np.nanmean, 0, current_table_err[argsort_indexes[:i], :])

        print i, gini_coeff(euploid_reconstruction), np.nanmean(euploid_reconstruction), np.nansum((euploid_reconstruction - speeds_err.reset_index().values[:, 1:][l_idx, :].astype(np.float))**2)

        speeds_v.loc['reconstruction %s'%i] = euploid_reconstruction
        speeds_err.loc['reconstruction %s'%i] = euploid_err_reconstruction
        pre_selector.append('reconstruction %s'%i)
        aneup_names.append('reconstruction %s'%i)

    # pre_selector = aneup_names
    re_index = dict([(name, _i) for _i, name in enumerate(aneup_names)])
    selector = np.array([re_index[pre_s] for pre_s in pre_selector])
    selector = np.array([re_index[pre_s] for pre_s in pre_selector])
    # errplot_with_selectors(speeds_v, speeds_err)


if __name__ == "__main__":
    canonical_mappings, canonical_replicas = build_unified_mappings(past_mappings)
    check_unified_mappings(canonical_mappings, canonical_replicas)
    # pprint(dict(canonical_mappings))
    spots_dict = build_spot_map()
    readout_map = pull_curves(canonical_mappings, canonical_replicas, spots_dict)
    collector, fails, conservation_object = iterate_through_conditions(readout_map)
    write_out_curves(collector, output_location)
    write_out_curves(fails, total_log)
    # pprint(conservation_object)
    dump(conservation_object, open('cons_obj.dmp', 'w'))

    conservation_object = load(open('cons_obj.dmp', 'r'))

    # reduce_table(conservation_object)

    regress_euploid(conservation_object)
