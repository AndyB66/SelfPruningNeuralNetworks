import os

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
# The GPU id to use, usually either "0" or "1"
# os.environ["CUDA_VISIBLE_DEVICES"] = "0"

import numpy as np
import tensorflow as tf
from tensorflow.keras import backend as kb
import tensorflow.keras, time, uuid, pickle, argparse, networks, utils

print("TF version:        ", tf.__version__)
print("TF.keras version:  ", tensorflow.keras.__version__)


def get_session(gpu_fraction=0.80):
    gpu_options = tf.compat.v1.GPUOptions(allow_growth=True)
    return tf.compat.v1.Session(config=tf.compat.v1.ConfigProto(gpu_options=gpu_options))


tf.compat.v1.keras.backend.set_session(get_session())

parser = argparse.ArgumentParser()
parser.add_argument('--outputpath', type=str, default="Outputs")
parser.add_argument("--trainweights", type=str, default='False', choices=["True", "False", "true", "false", "1", "0", "yes", "no", "y", "n"])
parser.add_argument("--trainmasks", type=str, default='True', choices=["True", "False", "true", "false", "1", "0", "yes", "no", "y", "n"])
parser.add_argument('--nettype', type=str, default='LeNet', choices=["LeNet", "Conv2", "Conv4", "Conv6"])
parser.add_argument('--p1', type=float, default=0.5)
parser.add_argument('--alpha', type=float, default=0)
parser.add_argument('--masktype', type=str, default='mask', choices=["mask", "mask_rs", "flip"])
parser.add_argument('--activation', type=str, default='relu', choices=["relu", "swish", "sigmoid", "elu", "selu"])
parser.add_argument('--initializer', type=str, default='he', choices=["glorot", "he", "heconstant", "binary"])
parser.add_argument('--lr', type=float, default=1e-3)
parser.add_argument('--batchsize', type=int, default=25)
parser.add_argument('--maxepochs', type=int, default=100)
parser.add_argument('--seed', type=int, default=0)

args = parser.parse_args()


def str2bool(v):
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    

def getmasks(net):
    masks = []

    for l in range(1, len(net.layers)):
        if isinstance(net.layers[l].get_weights(), list):
            masks.append([])
            continue

        m0 = net.layers[l].get_mask()
        m = np.ndarray.astype(m0, np.int8)
        nz = np.count_nonzero(m)
        masks.append(m)

    return masks


def getcountsperlayer(net):
    counts = []

    for l in range(1, len(net.layers)):
        w = net.layers[l].get_weights()
        if isinstance(w, list):
            continue

        m = net.layers[l].get_mask()
        NegativeMasks = np.count_nonzero(m < 0)
        PositiveMasks = np.count_nonzero(m > 0)
        ZeroMasks = np.count_nonzero(m == 0)

        mw = m * w
        NegativeMW = np.count_nonzero(mw < 0)
        PositiveMW = np.count_nonzero(mw > 0)
        ZeroMW = np.count_nonzero(mw == 0)

        counts.append([NegativeMasks, ZeroMasks, PositiveMasks, NegativeMW, ZeroMW, PositiveMW])

    return counts


def getcountstotal(net):
    nz_sum = 0
    total_sum = 0

    for l in range(1, len(net.layers)):
        if isinstance(net.layers[l].get_weights(), list):
            continue

        nz, total = net.layers[l].get_pruneamount()
        nz_sum += nz
        total_sum += total

    return nz_sum, total_sum


def NetworkTrainer(network, data, mypath, batchsize, maxepochs):
    if not os.path.exists(mypath):
        os.makedirs(mypath)
        print("data will be saved at", mypath)

    Xtrain, Ytrain, Xval, Yval, Xtest, Ytest, nclasses = data

    print(mypath)

    # save the network weights
    W = []
    for l in range(1, len(network.layers)):
        w = network.layers[l].get_weights()
        if isinstance(w, list):
            W.append([])
            continue

        W.append(w)

    # assign a unique run ID for the run
    RunID = uuid.uuid4().hex

    file = open(mypath + "Weights_ID" + RunID[-7:] + ".pkl", "wb")
    pickle.dump(W, file)
    file.close()

    epoch = 0

    print("\nEvaluate network with no training:")
    TrainL0, TrainA0 = network.evaluate(Xtrain, Ytrain, batch_size=200, verbose=2)
    TestL0, TestA0 = network.evaluate(Xtest, Ytest, batch_size=200, verbose=2)
    ValL0, ValA0 = network.evaluate(Xval, Yval, batch_size=200, verbose=2)

    TrainLoss = np.asarray([TrainL0])
    TrainAccuracy = np.asarray([TrainA0])

    TestLoss = np.asarray([TestL0])
    TestAccuracy = np.asarray([TestA0])

    ValLoss = np.asarray([ValL0])
    ValAccuracy = np.asarray([ValA0])

    remaining, total = getcountstotal(network)

    RemainingWeights = np.asarray([remaining])
    RemainingWeightsPerLayer = [getcountsperlayer(network)]

    runName = "_ID" + RunID[-7:]

    while epoch < maxepochs:
        start_time = time.time()

        print("\nepoch {}/{}".format(epoch + 1, maxepochs))

        loss, metric = network.metrics_names

        fit_history = network.fit(Xtrain, Ytrain, batch_size=batchsize, epochs=1, verbose=0, shuffle=True, validation_data=(Xval, Yval))

        TrainLoss = np.append(TrainLoss, fit_history.history[loss])
        ValLoss = np.append(ValLoss, fit_history.history['val_loss'])

        TrainAccuracy = np.append(TrainAccuracy, fit_history.history[metric])
        ValAccuracy = np.append(ValAccuracy, fit_history.history['val_' + metric])

        TestL0, TestA0 = network.evaluate(Xtest, Ytest, batch_size=200, verbose=0)
        TestLoss = np.append(TestLoss, TestL0)
        TestAccuracy = np.append(TestAccuracy, TestA0)

        remaining, total = getcountstotal(network)
        end_time = time.time()

        RemainingWeights = np.append(RemainingWeights, remaining)
        RemainingWeightsPerLayer.append(getcountsperlayer(network))
        sumall = np.sum(np.asarray(RemainingWeightsPerLayer[-1]), axis=0)

        nm, zm, pm = sumall[:3] / np.sum(sumall[:3])
        nw, zw, pw = sumall[3:] / np.sum(sumall[3:])

        print("Loss    - train, val, test:          {:.5f}, {:.5f}, {:.5f}".format(TrainLoss[-1], ValLoss[-1], TestLoss[-1]))
        print("Acc     - train, val, test:          {:.5f}, {:.5f}, {:.5f}".format(TrainAccuracy[-1], ValAccuracy[-1], TestAccuracy[-1]))
        print("Masks   - negative, zero, positive:  {:.5f}, {:.5f}, {:.5f}".format(nm, zm, pm))
        print("Weights - negative, zero, positive:  {:.5f}, {:.5f}, {:.5f}".format(nw, zw, pw))
        print("Execution time: {:.3f} seconds".format(end_time - start_time))
        print("=============================================================")

        epoch += 1

    Logs = {"trainLoss": TrainLoss,
            "testLoss": TestLoss,
            "valLoss": ValLoss,
            "trainAccuracy": TrainAccuracy,
            "testAccuracy": TestAccuracy,
            "valAccuracy": ValAccuracy,
            "remainingWeights": RemainingWeights,
            "remainingWeightsPerLayer": RemainingWeightsPerLayer}

    file = open(mypath + "Masks" + runName + ".pkl", "wb")
    pickle.dump(getmasks(network), file)
    file.close()

    file = open(mypath + "TrainLogs" + runName + ".pkl", "wb")
    pickle.dump(Logs, file)
    file.close()

    print("Files saved in", mypath)

    return 0


def PrepareMaskedMLP(data, myseed, initializer, activation, masktype, trainW, trainM, p1, alpha):
    dense_arch = [data[0].shape[-1], 300, 100, data[-1]]
    network = networks.makeMaskedMLP(dense_arch, activation, myseed, initializer, masktype, trainW, trainM, p1, alpha)
    return network


def PrepareConvolutional(csize, data, myseed, initializer, activation, masktype, trainW, trainM, p1, alpha):
    if csize == 2:
        return PrepareConv2(data, myseed, initializer, activation, masktype, trainW, trainM, p1, alpha)

    if csize == 4:
        return PrepareConv4(data, myseed, initializer, activation, masktype, trainW, trainM, p1, alpha)

    if csize == 6:
        return PrepareConv6(data, myseed, initializer, activation, masktype, trainW, trainM, p1, alpha)


def PrepareConv6(data, myseed, initializer, activation, masktype, trainW, trainM, p1, alpha):
    in_shape = data[0][0].shape

    kernelsize = 3
    cnn_arch = [[kernelsize, kernelsize, 3, 64], [kernelsize, kernelsize, 64, 64], [],
                [kernelsize, kernelsize, 64, 128], [kernelsize, kernelsize, 128, 128], [],
                [kernelsize, kernelsize, 128, 256], [kernelsize, kernelsize, 256, 256], []]

    dense_arch = [256, 256, data[-1]]
    network = networks.makeMaskedCNN(in_shape, cnn_arch, dense_arch, activation, myseed, initializer, masktype, trainW, trainM, p1, alpha)

    return network


def PrepareConv4(data, myseed, initializer, activation, masktype, trainW, trainM, p1, alpha):
    in_shape = data[0][0].shape

    kernelsize = 3
    cnn_arch = [[kernelsize, kernelsize, 3, 64], [kernelsize, kernelsize, 64, 64], [],
                [kernelsize, kernelsize, 64, 128], [kernelsize, kernelsize, 128, 128], []]

    dense_arch = [256, 256, data[-1]]
    network = networks.makeMaskedCNN(in_shape, cnn_arch, dense_arch, activation, myseed, initializer, masktype, trainW, trainM, p1, alpha)

    return network


def PrepareConv2(data, myseed, initializer, activation, masktype, trainW, trainM, p1, alpha):
    in_shape = data[0][0].shape

    kernelsize = 3
    cnn_arch = [[kernelsize, kernelsize, 3, 64], [kernelsize, kernelsize, 64, 64], []]
    dense_arch = [256, 256, data[-1]]
    network = networks.makeMaskedCNN(in_shape, cnn_arch, dense_arch, activation, myseed, initializer, masktype, trainW, trainM, p1, alpha)

    return network


def main(args):
    myseed = None if args.seed == 0 else args.seed
    p1 = args.p1
    lr = args.lr
    W = 1
    batchsize = args.batchsize
    maxepochs = args.maxepochs

    initializer = args.initializer
    activation = args.activation
    masktype = args.masktype

    train_weights = str2bool(args.trainweights)
    train_masks = str2bool(args.trainmasks)
    alpha = args.alpha
    trainingtype = "" + "TrainWeights" * train_weights + "TrainMasks" * train_masks
    outputpath = args.outputpath + "/" + trainingtype

    data = None
    network = None

    if "Conv" in args.nettype:
        csize = int(args.nettype[-1])

        # Pre-calculated W scaling factor (depends on the architecture)
        # See https://arxiv.org/abs/2006.16627 for how to calculate W
        if initializer == "binary":
            if csize == 6:
                W = 8.344820201940066e-12
            if csize == 4:
                W = 4.806616356300754e-09
            if csize == 2:
                W = 1.384305440187043e-06

        data = utils.SetMyData("CIFAR", W)
        outputpath += "/Conv" + str(csize)
        network = PrepareConvolutional(csize, data, myseed, initializer, activation, masktype, train_weights, train_masks, p1, alpha)

    if args.nettype == "LeNet":
        if initializer == "binary":
            W = 0.0005942073791483592

        data = utils.SetMyData("MNIST", W)
        outputpath += "/LeNet"
        network = PrepareMaskedMLP(data, myseed, initializer, activation, masktype, train_weights, train_masks, p1, alpha)

    outputpath += "/P1_" + str(p1) + "/alpha_" + str(alpha)
    outputpath += "/" + masktype + "_" + activation + "_" + initializer + "_LR" + str(lr)
    outputpath += "/" + "bs_" + str(batchsize) + "/"
    network.compile(loss='categorical_crossentropy', optimizer=tf.keras.optimizers.Adam(lr=lr), metrics=['accuracy'])
    network.summary()
    NetworkTrainer(network, data, outputpath, batchsize, maxepochs)
    kb.clear_session()

    return


if __name__ == '__main__':
    main(args)
