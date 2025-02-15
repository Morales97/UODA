from __future__ import print_function

import argparse
import os
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.autograd import Variable
from model.resnet import resnet34, resnet50
from model.basenet import AlexNetBase, VGGBase, Predictor, Predictor_deep
from utils.utils import weights_init
from utils.lr_schedule import inv_lr_scheduler
from utils.return_dataset import return_dataset
from utils.loss import entropy, adentropy
import time
import pdb
#os.environ['CUDA_VISIBLE_DEVICES'] = "0"
# Training settings
parser = argparse.ArgumentParser(description='Visda Classification')
parser.add_argument('--steps', type=int, default=20001, metavar='N',
                    help='number of iterations to train (default: 50000)')
parser.add_argument('--method', type=str, default='UODA', choices=['S+T', 'ENT', 'MME', 'UODA'],
                    help='MME is proposed method, ENT is entropy minimization, S+T is training only on labeled examples')
parser.add_argument('--lr', type=float, default=0.01, metavar='LR',
                    help='learning rate (default: 0.001)')
parser.add_argument('--multi', type=float, default=0.1, metavar='MLT',
                    help='learning rate multiplication')
parser.add_argument('--T', type=float, default=0.05, metavar='T',
                    help='temperature (default: 0.05)')
parser.add_argument('--lamda', type=float, default=0.1, metavar='LAM',
                    help='value of lamda')
parser.add_argument('--save_check', action='store_true', default=False,
                    help='save checkpoint or not')
parser.add_argument('--checkpath', type=str, default='./save_model_ssda',
                    help='dir to save checkpoint')
parser.add_argument('--seed', type=int, default=1, metavar='S',
                    help='random seed (default: 1)')
parser.add_argument('--log-interval', type=int, default=100, metavar='N',
                    help='how many batches to wait before logging training status')
parser.add_argument('--save_interval', type=int, default=500, metavar='N',
                    help='how many batches to wait before testing and saving a model')
parser.add_argument('--net', type=str, default='resnet34', metavar='B',
                    help='which network to use')
parser.add_argument('--source', type=str, default='real', metavar='B',
                    help='source domain')
parser.add_argument('--target', type=str, default='sketch', metavar='B',
                    help='target domain')
parser.add_argument('--dataset', type=str, default='multi', choices=['multi','office', 'office_home'],
                    help='the name of dataset')
parser.add_argument('--bs', type=int, default=24, metavar='S',
                    help='batchsize')

parser.add_argument('--num', type=int, default=3,
                    help='number of labeled examples in the target')
parser.add_argument('--patience', type=int, default=5, metavar='S',
                    help='early stopping to wait for improvment '
                         'before terminating. (default: 5 (5000 iterations))')
parser.add_argument('--early', action='store_false', default=False,
                    help='early stopping on validation or not')

args = parser.parse_args()
print('dataset %s source %s target %s network %s' % (args.dataset, args.source, args.target, args.net))
#source_loader, target_loader, target_loader_unl, class_list = return_dataset(args)
source_loader, target_loader, target_loader_unl, target_loader_val, \
    target_loader_test, class_list = return_dataset(args)
use_gpu = torch.cuda.is_available()
record_dir = 'record/%s/%s' % (args.dataset, args.method)
if not os.path.exists(record_dir):
    os.makedirs(record_dir)
record_file = os.path.join(record_dir,
                           '%s_net_%s_%s_to_%s_lamda_%s' % (
                           args.method, args.net, args.source, args.target, args.lamda))
record_dir_train = 'record_train/%s/%s' % (args.dataset, args.method)
if not os.path.exists(record_dir_train):
    os.makedirs(record_dir_train)

torch.cuda.manual_seed(args.seed)
if args.net == 'resnet34':
    G = resnet34()
    inc = 512
elif args.net == 'resnet50':
    G = resnet50()
    inc = 2048
elif args.net == "alexnet":
    G = AlexNetBase()
    inc = 4096
elif args.net == "vgg":
    G = VGGBase()
    inc = 4096
else:
    raise ValueError('Model cannot be recognized.')


params = []
for key, value in dict(G.named_parameters()).items():
    if value.requires_grad:
        if 'bias' in key:
            params += [{'params': [value], 'lr': args.multi, 'weight_decay': 0.0005}]
        else:
            params += [{'params': [value], 'lr': args.multi, 'weight_decay': 0.0005}]

if "resnet" in args.net:
    F1 = Predictor_deep(num_class=len(class_list),
                        inc=inc)
    F2 = Predictor_deep(num_class=len(class_list),
                        inc=inc)

else:
    F1 = Predictor_deep(num_class=len(class_list), inc=inc, temp=args.T)
    F2 = Predictor_deep(num_class=len(class_list), inc=inc, temp=args.T)


weights_init(F1)
weights_init(F2)

lr = args.lr
G.cuda()
F1.cuda()
F2.cuda()

im_data_s = torch.FloatTensor(1)
im_data_t = torch.FloatTensor(1)
im_data_tu = torch.FloatTensor(1)
gt_labels_s = torch.LongTensor(1)
gt_labels_t = torch.LongTensor(1)
sample_labels_t = torch.LongTensor(1)
sample_labels_s = torch.LongTensor(1)

im_data_s = im_data_s.cuda()
im_data_t = im_data_t.cuda()
im_data_tu = im_data_tu.cuda()
gt_labels_s = gt_labels_s.cuda()
gt_labels_t = gt_labels_t.cuda()
sample_labels_t = sample_labels_t.cuda()
sample_labels_s = sample_labels_s.cuda()

im_data_s = Variable(im_data_s)
im_data_t = Variable(im_data_t)
im_data_tu = Variable(im_data_tu)
gt_labels_s = Variable(gt_labels_s)
gt_labels_t = Variable(gt_labels_t)
sample_labels_t = Variable(sample_labels_t)
sample_labels_s = Variable(sample_labels_s)

if os.path.exists(args.checkpath) == False:
    os.mkdir(args.checkpath)


def train():
    G.train()
    F1.train()
    F2.train()

    optimizer_g = optim.SGD( G.parameters(), lr = args.multi, momentum=0.9, weight_decay=0.0005,
                            nesterov=True)
    optimizer_f = optim.SGD(list(F1.parameters()), lr=1.0, momentum=0.9, weight_decay=0.0005,
                            nesterov=True)
    optimizer_f2 = optim.SGD(list(F2.parameters()), lr=1.0, momentum=0.9, weight_decay=0.0005,
                            nesterov=True)
    def zero_grad_all():
        optimizer_g.zero_grad()
        optimizer_f.zero_grad()
        optimizer_f2.zero_grad()
    param_lr_g = []
    for param_group in optimizer_g.param_groups:
        param_lr_g.append(param_group["lr"])
    param_lr_f = []
    for param_group in optimizer_f.param_groups:
        param_lr_f.append(param_group["lr"])

    param_lr_f2 = []
    for param_group in optimizer_f2.param_groups:
        param_lr_f2.append(param_group["lr"])

    criterion = nn.CrossEntropyLoss().cuda()
    all_step = args.steps
    data_iter_s = iter(source_loader)
    data_iter_t = iter(target_loader)
    data_iter_t_unl = iter(target_loader_unl)
    len_train_source = len(source_loader)
    len_train_target = len(target_loader)
    len_train_target_semi = len(target_loader_unl)

    best_acc = 0
    best_acc_test = 0
    counter = 0

    time_last = time.time()
    time_last_save = time.time()

    for step in range(all_step):

        optimizer_g = inv_lr_scheduler(param_lr_g, optimizer_g, step, init_lr=args.lr)
        optimizer_f = inv_lr_scheduler(param_lr_f, optimizer_f, step, init_lr=args.lr)
        optimizer_f2 = inv_lr_scheduler(param_lr_f2, optimizer_f2, step, init_lr=args.lr)

        lr = optimizer_f2.param_groups[0]['lr']
        
        if step % len_train_target == 0:
            data_iter_t = iter(target_loader)
        if step % len_train_target_semi == 0:
            data_iter_t_unl = iter(target_loader_unl)
        if step % len_train_source == 0:
            data_iter_s = iter(source_loader)
        data_t = next(data_iter_t)
        data_t_unl = next(data_iter_t_unl)
        data_s = next(data_iter_s)
        '''
        im_data_s.data.resize_(data_s[0].size()).copy_(data_s[0])
        gt_labels_s.data.resize_(data_s[1].size()).copy_(data_s[1])
        im_data_t.data.resize_(data_t[0].size()).copy_(data_t[0])
        gt_labels_t.data.resize_(data_t[1].size()).copy_(data_t[1])
        im_data_tu.data.resize_(data_t_unl[0].size()).copy_(data_t_unl[0])
        '''
        im_data_s = data_s[0].cuda()
        gt_labels_s = data_s[1].cuda()
        im_data_t = data_t[0].cuda()
        gt_labels_t = data_t[1].cuda()
        im_data_tu = data_t_unl[0].cuda()
        zero_grad_all()
        
        data = torch.cat((im_data_s, im_data_t), 0)
        target = torch.cat((gt_labels_s, gt_labels_t), 0)

        output_s = G(im_data_s)
        out_s = F1(output_s)

        output_t = G(im_data_t)
        out_t = F1(output_t)



        loss_s = criterion(out_s, gt_labels_s) 
        loss_t = criterion(out_t, gt_labels_t) 


        loss = 0.75 * loss_s + 0.25 * loss_t

        loss.backward()
        optimizer_f.step()
        optimizer_g.step()
        zero_grad_all()

        output_t = G(im_data_t)
        out_t = F2(output_t)

        output_s = G(im_data_s)
        out_s = F2(output_s)

        out_t1 = F1(output_t)


        loss_s = criterion(out_s, gt_labels_s) 
        loss_t = criterion(out_t, gt_labels_t) 

        
        loss = (0.25 * loss_s + 0.75 * loss_t) 

        loss.backward()
        optimizer_f2.step()
        optimizer_g.step()
        zero_grad_all()
        

        if not args.method == 'S+T':
            output_t = G(im_data_tu)
            output_s = G(im_data_s)

            if args.method == 'ENT':
                loss_t = entropy(F2, output, args.lamda)
                loss_t.backward()
                optimizer_f2.step()
                optimizer_g.step()

            elif args.method == 'UODA':
                loss_t =  adentropy(F2, output_t, args.lamda, s='tar')
                loss_t.backward()
                optimizer_f2.step()
                optimizer_g.step()

                loss_s = 1 *adentropy(F1, output_s, args.lamda, s='src')
                loss_s.backward()
                optimizer_f.step()
                optimizer_g.step()

            else:
                raise ValueError('Method cannot be recognized.')
            log_train = 'S {} T {} Train Ep: {} lr{} \t Loss Classification: {:.6f} Loss T {:.6f} Loss S {:.6f} Method {}\n'.format(
                args.source, args.target,
                step, lr, loss.data, -loss_t.data, -loss_s.data, args.method)
        else:
            log_train = 'S {} T {} Train Ep: {} lr{} \t Loss Classification: {:.6f} Method {}\n'.format(
                args.source, args.target,
                step, lr, loss.data, args.method)


        # time_after_one_step = time.time() - time_before
        # print('The {} step takes {:.0f}m {:.0f}s'.format(step, time_after_one_step // 60, time_after_one_step % 60))

        G.zero_grad()
        F1.zero_grad()
        F2.zero_grad()
        zero_grad_all()
        if step % args.log_interval == 0:

            print(log_train)

            time_for_one_logging = time.time() - time_last
            time_last = time.time()
            print('The {} logging takes {:.0f}m {:.0f}s'.format(int(step/args.log_interval), time_for_one_logging // 60, time_for_one_logging % 60))
            

        if step % args.save_interval == 0 and step > 0:
            loss_test, acc_test = test(target_loader_test)
            loss_val, acc_val = test(target_loader_val)

            G.train()
            F1.train()
            F2.train()
            if acc_val > best_acc:
                best_acc = acc_val
            if acc_test > best_acc_test:
                best_acc_test = acc_test
                counter = 0
            else:
                counter += 1
            if args.early:
                if counter > args.patience:
                    break
            print('best acc test %f best acc val %f' % (best_acc_test,
                                                        acc_val))
            print('record %s' % record_file)
            with open(record_file, 'a') as f:
                f.write('step %d best %f final %f \n' % (step,
                                                         best_acc_test,
                                                         acc_val))
            # if args.save_check:
            #     if not os.path.exists(args.save_check):
            #         os.makedirs(args.save_check)

            #     print('saving model')
            #     torch.save(G.state_dict(), os.path.join(args.checkpath,
            #                                               "G_iter_model_{}_{}_to_{}_step_{}.pth.tar".format(
            #                                                   args.method, args.source, args.target, step)))
            #     torch.save(F2.state_dict(),
            #                os.path.join(args.checkpath, "F2_iter_model_{}_{}_to_{}_step_{}.pth.tar".format(
            #                    args.method, args.source, args.target, step)))

            time_for_one_saving = time.time() - time_last_save
            time_last_save = time.time()
            print('The {} saving takes {:.0f}m {:.0f}s'.format(int(step/args.save_interval), time_for_one_saving // 60, time_for_one_saving % 60))
            print('estimated needed time: {:.0f}m {:.0f}s'.format( (all_step - step)*time_for_one_saving/args.save_interval // 60, (all_step - step)*time_for_one_saving/args.save_interval % 60))

def test(loader):
    G.eval()
    F1.eval()
    F2.eval()
    test_loss = 0
    correct = 0
    size = 0
    num_class = len(class_list)
    output_all = np.zeros((0, num_class))
    criterion = nn.CrossEntropyLoss().cuda()
    confusion_matrix = torch.zeros(num_class, num_class)
    with torch.no_grad():
        for batch_idx, data_t in enumerate(loader):
            im_data_t.data.resize_(data_t[0].size()).copy_(data_t[0])
            gt_labels_t.data.resize_(data_t[1].size()).copy_(data_t[1])
            feat = G(im_data_t)

            output1 =  F2(feat) + F1(feat)
            output_all = np.r_[output_all, output1.data.cpu().numpy()]
            size += im_data_t.size(0)
            pred1 = output1.data.max(1)[1]  # get the index of the max log-probability
            for t, p in zip(gt_labels_t.view(-1), pred1.view(-1)):
                confusion_matrix[t.long(), p.long()] += 1
            correct += pred1.eq(gt_labels_t.data).cpu().sum()
            test_loss += criterion(output1, gt_labels_t) / len(loader)
    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} F2 ({:.0f}%)\n'.format(
        test_loss, correct, size,
        100. * correct / size))
    return test_loss.data, 100. * float(correct) / size


train()
