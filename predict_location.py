import argparse
import json
import os
import numpy
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

from torch.autograd import Variable
from data_loader import Landmarks, FasttextFeatures, ResnetFeatures, GoldstandardFeatures, \
                        load_data, load_features, create_obs_dict, create_batch
from models import MapEmbedding
from utils import create_logger

class LocationPredictor(nn.Module):

    def __init__(self, resnet_features, fasttext_features,
                 emb_sz, num_embeddings):
        super(LocationPredictor, self).__init__()
        self.resnet_features = resnet_features
        self.fasttext_features = fasttext_features
        self.num_embeddings = num_embeddings
        self.emb_sz = emb_sz
        if self.fasttext_features:
            self.fasttext_emb_linear = nn.Linear(300, emb_sz)
        if self.resnet_features:
            self.resnet_emb_linear = nn.Linear(2048, emb_sz)
        self.emb_map = MapEmbedding(num_embeddings, emb_sz, init_std=0.01)
        self.loss = nn.CrossEntropyLoss()

    def forward(self, X, landmarks, y):
        batch_size = y.size(0)
        if self.resnet_features:
            resnet_emb = self.resnet_emb_linear.forward(X['resnet'])
            resnet_emb = resnet_emb.sum(dim=1)

        if self.fasttext_features:

            fasttext_emb = self.fasttext_emb_linear.forward(X['fasttext'])
            fasttext_emb = fasttext_emb.sum(dim=1)

        if self.resnet_features and self.fasttext_features:
            emb = resnet_emb + fasttext_emb
        elif self.resnet_features:
            emb = resnet_emb
        else:
            emb = fasttext_emb

        # print(input_emb)
        l_emb = self.emb_map.forward(landmarks)

        logits = []
        for i in range(batch_size):
            # print(input_emb[i, :10])
            score = torch.matmul(l_emb[i, :, :], emb[i, :])
            logits.append(score.unsqueeze(0))
        logits = torch.cat(logits)

        prob = F.softmax(logits, dim=1)
        loss = self.loss(prob, y.squeeze())

        acc = sum([1.0 for pred, target in zip(prob.multinomial(1).data.cpu().numpy(), y.data.cpu().numpy()) if pred[0] == target[0]])/batch_size
        return loss, acc

    def save(self, path):
        state = dict()
        state['feat_sz'] = self.feat_sz
        state['embed_sz'] = self.embed_sz
        state['num_embeddings'] = self.num_embeddings
        state['parameters'] = self.state_dict()
        torch.save(state, path)

    @classmethod
    def load(cls, path):
        state = torch.load(path)
        model = cls(state['feat_sz'], state['embed_sz'], state['num_embeddings'])
        model.load_state_dict(state['parameters'])
        return model


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cuda', action='store_true')
    parser.add_argument('--resnet-features', action='store_true')
    parser.add_argument('--fasttext-features', action='store_true')
    parser.add_argument('--big-softmax', action='store_true')
    parser.add_argument('--emb-sz', type=int, default=512)
    parser.add_argument('--num-epochs', type=int, default=500)
    parser.add_argument('--batch_sz', type=int, default=64)
    parser.add_argument('--exp-name', type=str, default='test')

    args = parser.parse_args()

    print(args)

    data_dir = os.environ.get('TALKTHEWALK_DATADIR', './data')

    train_configs = json.load(open(os.path.join(data_dir, 'configurations.train.json')))
    valid_configs = json.load(open(os.path.join(data_dir, 'configurations.valid.json')))
    test_configs = json.load(open(os.path.join(data_dir, 'configurations.test.json')))

    numpy.random.shuffle(train_configs)

    neighborhoods = ['fidi', 'hellskitchen', 'williamsburg', 'uppereast', 'eastvillage']
    landmark_map = Landmarks(neighborhoods, include_empty_corners=True)

    feature_loaders = dict()
    if args.fasttext_features:
        textfeatures = load_features(neighborhoods)
        obs_i2s, obs_s2i = create_obs_dict(textfeatures, neighborhoods)
        feature_loaders['fasttext'] = FasttextFeatures(textfeatures, '/private/home/harm/data/wiki.en.bin')
    if args.resnet_features:
        feature_loaders['resnet'] = ResnetFeatures(os.path.join(data_dir, 'resnetfeat.json'))
    assert (len(feature_loaders) > 0)

    X_train, landmark_train, y_train = load_data(train_configs, feature_loaders, landmark_map)
    X_valid, landmark_valid, y_valid = load_data(valid_configs, feature_loaders, landmark_map)
    X_test, landmark_test, y_test = load_data(test_configs, feature_loaders, landmark_map)


    net = LocationPredictor(args.resnet_features, args.fasttext_features, args.emb_sz, len(landmark_map.idx_to_global_coord))
    params = [v for k, v in net.named_parameters()]
    print(len(params))
    opt = optim.Adam(params, lr=1e-4)

    X_train, landmark_train, _, y_train = create_batch(X_train, landmark_train, y_train,
                                                       cuda=args.cuda)


    X_valid, landmark_valid, _, y_valid = create_batch(X_valid, landmark_valid, y_valid,
                                                       cuda=args.cuda)

    X_test, landmark_test, _, y_test = create_batch(X_test, landmark_test, y_test,
                                                       cuda=args.cuda)

    if args.cuda:
        net.cuda()

    for i in range(args.num_epochs):
        train_loss, train_acc = net.forward(X_train, landmark_train, y_train)

        opt.zero_grad()
        train_loss.backward()
        opt.step()

        valid_loss, valid_acc = net.forward(X_valid, landmark_valid, y_valid)
        test_loss, test_acc = net.forward(X_test, landmark_test, y_test)

        print("Train loss: {} | Valid loss: {} | Test loss: {}".format(train_loss.cpu().data.numpy()[0],
                                                                       valid_loss.cpu().data.numpy()[0],
                                                                       test_loss.cpu().data.numpy()[0]))
        print("Train acc: {} | Valid acc: {} | Test acc: {}".format(train_acc,
                                                                    valid_acc,
                                                                    test_acc))
