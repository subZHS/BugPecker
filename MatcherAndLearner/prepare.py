import logging
from sklearn.utils import shuffle
from gensim.models.word2vec import Word2Vec
from utils.data_utils import *
from utils.feature_utils import *


def word_dictionary_and_embedding(reports, repo_save_root):
    word_embedding_path = repo_save_root + 'word_w2v_128'
    corpus = reports['report']
    if not os.path.exists(word_embedding_path):
        w2v = Word2Vec(corpus, size=128, workers=16, sg=1, max_final_vocab=5000)
        w2v.save(word_embedding_path)
    else:
        w2v = Word2Vec.load(word_embedding_path)
    vocab = w2v.wv.vocab
    max_token = w2v.wv.syn0.shape[0]  # 字典的单词总量

    def token2index(token_list):
        index_list = []
        for token in token_list:
            index_list.append([vocab[token].index if token in vocab else max_token])
        return index_list

    reports['report_ids'] = reports['report'].apply(token2index)  # word tokens to indexes
    return reports


def over_sampling(dataset, k):
    positive = dataset[dataset['label'] == 1]
    for i in range(0, k):
        dataset = dataset.append(positive)
    dataset = shuffle(dataset)
    return dataset


def bfr_and_bff_features(method, br_date, bug_reports):
    # 找出修改过该method的并在该report之前被创建的所有reports
    prev_reports = previous_reports_for_mlevel(method, br_date, bug_reports)

    # Bug Fixing Recency
    bfr = bug_fixing_recency_for_mlevel(br_date, prev_reports)

    # Bug Fixing Frequency
    bff = len(prev_reports)
    return float(bfr), float(bff)


def generate_data(wrong_k, reports, all_reports, repo_blocks_url, data_save_path, set_name,
                            commit2commit, method2method, method_call_method, method_call_graph):

    features = []

    for index, row in reports.iterrows():

        cur_report = row['report']
        bug_id = row['bug_id']
        commit_id = row['commit_id']
        report_ids = row['report_ids']
        methods = row['method']
        br_time = row['commit_time']
        blocks_path = repo_blocks_url + commit_id + '.pkl'

        if not os.path.exists(blocks_path):
            print('commit id not exists!')
            continue
        else:
            blocks = pd.read_pickle(blocks_path)

        # check the ground truth
        unused_method = 0
        for method in methods:
            method_block = blocks[blocks.index == method]
            if len(method_block) == 0:
                unused_method += 1
                continue

        if set_name == 'test':

            prev_reports = all_reports[all_reports['commit_time'] < br_time]
            method_cfs = collaborative_filtering_score_for_mlevel(cur_report, prev_reports, 50, True, commit2commit,
                                                                  True, method2method)
        elif set_name == 'train':
            prev_reports = reports[reports['commit_time'] < br_time]
            method_cfs = collaborative_filtering_score_for_mlevel(cur_report, prev_reports, 50, True, commit2commit,
                                                                  True, method2method)

        for method in methods:
            method_block = blocks[blocks.index == method]
            if len(method_block) == 0:
                continue
            code_ids = method_block.values[0]
            if set_name == 'train':
                bfr, brr = bfr_and_bff_features(method, br_time, reports)
            elif set_name == 'test':
                bfr, brr = bfr_and_bff_features(method, br_time, all_reports)

            if len(method_block) <= 5:
                expand_method_set = get_related_methods_to_expand_short_method(method, method2method,
                                                                               method_call_method, method_call_graph,
                                                                               10)
            expand_method_codes = []
            for expand_method in expand_method_set:
                expand_code = blocks[blocks.index == expand_method]
                if len(expand_code) == 1:
                    expand_code = expand_code.values[0]
                    expand_method_codes.append(expand_code)

            if method in method_cfs:
                cfs = method_cfs[method]
            else:
                cfs = 0.0

            line = [bug_id, method, report_ids, code_ids, bfr, brr, cfs, expand_method_codes, 1]

            features.append(line)

        #  negative sample
        all_methods = blocks.index.tolist()
        if set_name == 'train':
            wrong_methods = random_k_wrong_files(methods, all_methods, k=wrong_k)
        elif set_name == 'test':
            wrong_methods = list(set(blocks.index.tolist()) - set(methods))

        for wrong_method in wrong_methods:
            code_ids = blocks[blocks.index == wrong_method].values[0]
            if set_name == 'train':
                bfr, brr = bfr_and_bff_features(wrong_method, row, br_time, reports)
            elif set_name == 'test':
                bfr, brr = bfr_and_bff_features(wrong_method, row, br_time, all_reports)

            if wrong_method in method_cfs:
                cfs = method_cfs[wrong_method]
            else:
                cfs = 0.0
            if len(method_block) <= 5:
                expand_method_set = get_related_methods_to_expand_short_method(wrong_method, method2method,
                                                                               method_call_method, method_call_graph,
                                                                               10)
            expand_method_codes = []
            for expand_method in expand_method_set:
                expand_code = blocks[blocks.index == expand_method]
                if len(expand_code) == 1:
                    expand_code = expand_code.values[0]
                    expand_method_codes.append(expand_code)

            line = [bug_id, method, report_ids, code_ids, bfr, brr, cfs, expand_method_codes,0]
            features.append(line)

        del blocks
        gc.collect()

    features = pd.DataFrame(features, columns=['bug_id', 'method_name', 'report_ids', 'code_ids', 'bfr',
                                              'brr', 'cfs', 'expand_codes', 'is_bias', 'label'])

    if set_name == 'train':
        features = over_sampling(features, wrong_k)

    features.to_pickle(data_save_path)


def prepare_data(args, config):

    logger = logging.getLogger('BugLoc')
    logger.info('preparing train set and test set...')
    data = pd.read_csv(config.project_csv)
    data['report'] = data.apply(lambda row: process_report_text(row['summary'], row['description']), axis=1)
    data = word_dictionary_and_embedding(data, config.data_path)

    length = len(data)
    train_size = int(length * args.tr)
    test_size = length - train_size
    assert length == train_size + test_size
    train = data.head(train_size)
    test = data.tail(test_size)
    logger.info('size of train data:', len(train))
    logger.info('size of test data:', len(test))

    repo_block_path = config.block_path
    repo_sim_path = config.sim_path

    train_set_path = config.data_path + 'train.pkl'
    test_set_path = config.data_path + 'test.pkl'

    commit2commit, method2method, method_call_method, method_call_graph = load_sim_files(repo_sim_path)

    if not os.path.exists(train_set_path):
        logger.info('generate train set...')
        generate_data(wrong_k=300, reports=train, all_reports=None, repo_blocks_url=repo_block_path,
                                data_save_path=train_set_path, set_name='train', commit2commit=commit2commit,
                                method2method=method2method, method_call_method=method_call_method,
                                method_call_graph=method_call_graph)
    else:
        logger.info('train set exists......')

    if not os.path.exists(test_set_path):
        logger.info('generate test set......')
        generate_data(wrong_k=0, reports=test, all_reports=data, repo_blocks_url=repo_block_path,
                                data_save_path=test_set_path, set_name='test', commit2commit=commit2commit,
                                method2method=method2method, method_call_method=method_call_method,
                                method_call_graph=method_call_graph)
    else:
        logger.info('test set exists......')