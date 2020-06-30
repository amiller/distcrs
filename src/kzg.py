from ssbls12 import Fp, Poly, Group
G = Group.G
GT = Group.GT
import asyncio
import random
from functools import partial
from router import TestRouter
from pickle import dumps
from hashlib import sha256

def print_exception_callback(future):
    if future.done():
        ex = future.exception()
        if ex is not None:
            raise ex


def random_fp():
    return Fp(random.randint(0, Fp.p-1))

def hash_to_fp(bytestr):
    assert type(bytestr) is bytes
    return Fp(int(sha256(bytestr).hexdigest(), 16)%Fp.p)

#Check if the two pairs differ by the same exponent
#Sym suffix denotes the symmetric version of X function
def SameRatioSym(pair1, pair2):
    g1, h1, g2, h2 = pair1 + pair2
    return Group.pair(g1, h2) == Group.pair(h1, g2)

def SameRatioSeqSymOld(sequence, pair):
    for i in range(len(sequence)-1):
        if not SameRatioSym([sequence[i], sequence[i+1]], pair):
            return False
    return True

#Use randomization to minimize pairings. A few times faster than SameRatioSeqSymOld
#(would be even faster to use a multiexponentiation)
def SameRatioSeqSym(sequence, pair):
    left = G * Fp(0)
    right = G * Fp(0)
    l = len(sequence)
    blinds = [random_fp() for i in range(l-1)]
    for i in range(l-1):
        left += sequence[i+1] * blinds[i]
        right += sequence[i] * blinds[i]
    return Group.pair(pair[0],left) == Group.pair(pair[1], right)

def ProveDL(pair, s):
    r = random_fp()
    g, h = pair
    R = g*r
    c = hash_to_fp(dumps(R))
    u = r + c*s
    return [R,u]

def VerifyDL(pair, pi):
    R, u = pi
    g, h = pair
    c = hash_to_fp(dumps(R))
    return g*u == R + h*c

class Blockchain:
    def __init__(self, send, recv):
        self.blocks = []
        self.send = send
        self.recv = recv
    async def run(self):
        while True:
            sender, msg = await self.recv()
            self.process_msg(msg, sender)
            if msg[0] == "BREAK":
                break
    def process_msg(self, msg, sender):
        if msg[0] == "GET":
            blocknum = msg[1]
            if blocknum == "ALL":
                self.send(sender, self.blocks)
            else:
                self.send(sender, self.blocks[blocknum])
        if msg[0] == "SET":
            self.blocks.append(msg[1])
            self.send(sender, 'ACK')
            #print(self.blocks)
            print("block " + str(len(self.blocks)-1) + " posted")
        if msg[0] == "SET_NOWAIT":
            self.blocks.append(msg[1])
            #print(self.blocks)
            print("block " + str(len(self.blocks)-1) + " posted")
        if msg[0] == "LEN":
            self.send(sender, len(self.blocks))
            #print(len(self.blocks))
        if msg[0] == "PRINT":
            print(self.blocks)               
            

def get_routing(n):
    router = TestRouter(n)
    return [router.sends, router.recvs]

def gen_blockchain_funcs(sends, recvs):
    n = len(sends)
    async def get_len(id, send, recv):
        send(0, ["LEN"])
        while True:
            sender, msg = await recv()
            #if the message isn't from the blockchain, receive it again later
            #TODO: This changes who the sender is, which could be a problem (maybe add a re-receive function?)
            if sender != 0:
                send(id, msg)
                continue
            break
        return msg
    #def check_for_work(id) (maybe it can go in player?)
    async def get_block(id, send, recv, blockindex):
        send(0, ["GET", blockindex])
        while True:
            sender, msg = await recv()
            if sender != 0:
                send(id, msg)
                continue
            break
        return msg
    async def post_block(id, send, recv, msg):
        send(0, ["SET", msg])
        #wait for acknowledgement from the chain
        while True:
            sender, msg = await recv()
            if sender != 0 or msg != 'ACK':
                send(id, msg)
                continue
            break
    get_lens = [partial(get_len, i, sends[i], recvs[i]) for i in range(n)]
    get_blocks = [partial(get_block, i, sends[i], recvs[i]) for i in range(n)]
    post_blocks = [partial(post_block, i, sends[i], recvs[i]) for i in range(n)]
    outs = [[get_lens[i], get_blocks[i], post_blocks[i]] for i in range(n)] 
    return outs

class KZGPlayer:
    def __init__(self, i, alpha_i, send, recv, bcfuncs, options=None):
        self.i = i
        self.alpha_i = alpha_i
        self.send = send
        self.recv = recv
        self.bc_len, self.bc_get_block, self.bc_post_block = bcfuncs
        if options is None:
            options = {
                'mode': 'robust',
                'role': 'checkpoint',
                'pl_thresh': None
            }
        self.role = options['role']
        self.mode = options['mode']
    async def recv_inc(self):
        while True:
            sender, msg = await self.recv()
            if type(msg) is not list or msg[0] != "INC":
                self.send(self.i, msg)
                continue
            return msg[1:]
        
    async def run(self):
        if self.mode == 'robust':
            while True:
                msg = await self.bc_len()
                if msg == self.i:
                    _, crs = await self.bc_get_block(self.i-1)
                    newcrs = inc_kzg(crs, self.alpha_i)
                    blockchain = await self.bc_get_block("ALL")
                    print(verify_chain_kzg(blockchain))
                    pi = prove_knowledge_kzg(crs[1], newcrs[1], self.alpha_i)
                    await self.bc_post_block([pi,newcrs])
                    break
                await asyncio.sleep(.5)
        if self.mode == 'optimistic':
            proofs, crs = await self.recv_inc()
            newcrs = inc_kzg(crs, self.alpha_i)
            if proofs is None:
                newproof = prove_knowledge_kzg(crs[1], newcrs[1], self.alpha_i)
            else:
                newproof = inc_knowledge_proof_kzg(proofs, self.alpha_i)
            if self.role == 'checkpoint':
                blockchain = await self.bc_get_block(-1)
                newblock = [newproof, newcrs]
                #print(verify_chain_kzg(blockchain + [newblock]))
                print(verify_chain_kzg([blockchain, newblock]))
                await self.bc_post_block(newblock)
                self.send(self.i+1, ["INC", None, newcrs])
            elif self.role == 'passthrough':
                self.send(self.i+1, ["INC", newproof, newcrs])
                
                

def init_kzg(alpha, crslen):
    assert crslen > 1
    crs = [G]
    for i in range(crslen-1):
        crs.append(crs[-1] * alpha)
    return crs

def inc_kzg(crs, alpha_i):
    #note: creating a new crs copy here to avoid bugs
    alphapow = 1
    newcrs = [None]*len(crs)
    for j in range(len(crs)):
        newcrs[j] = crs[j] * alphapow
        alphapow *= alpha_i
    return newcrs

def ver_integrity_kzg(crs):
    pair = [crs[0], crs[1]]
    return SameRatioSeqSym(crs, pair)

#verify a chain of knoledge proofs
#here a proof is really [new g^alpha, DLProof]
def ver_knowledge_kzg(oldcrs, newcrs, proofs):
    if proofs[-1][0] != newcrs[1]:
        return False
    lastbase = oldcrs[1]
    for proof in proofs:
        if not VerifyDL([lastbase, proof[0]], proof[1]):
            return False
        lastbase = proof[0]
    return True

def prove_knowledge_kzg(old_g_alpha, new_g_alpha, alpha):
    #start a list of proofs (which are themselves lists) for easy appending later
    return [ [new_g_alpha, ProveDL([old_g_alpha, new_g_alpha], alpha)] ]

def inc_knowledge_proof_kzg(proofs, alpha):
    old_g_alpha = proofs[-1][0]
    new_g_alpha = old_g_alpha * alpha
    proofs.append([new_g_alpha, ProveDL([old_g_alpha, new_g_alpha], alpha)])
    return proofs

#This technically only needs the second element of each step along with the proofs
#it only needs the full crs for the last step
def verify_chain_kzg(blockchain):
    #part 1: ensure final scructure is correct
    _, lastcrs = blockchain[-1]
    if not ver_integrity_kzg(lastcrs):
        return False
    #part 2: ensure each block was built off of the last
    for i in range(1, len(blockchain)):
        proof, crs = blockchain[i]
        _, crsold = blockchain[i-1]
        if not ver_knowledge_kzg(crsold, crs, proof):
            return False
    return True

if __name__ == "__main__":
    numplayers = 9
    crslen = 5
    test = 'robust'
    test = 'optimistic'
    
    n = numplayers+1
    sends, recvs = get_routing(n)
    bcfuncs = gen_blockchain_funcs(sends, recvs)
    bc = Blockchain(sends[0], recvs[0])
    loop = asyncio.get_event_loop()
    alphas = [random_fp() for i in range(n)]

    #assume a known alpha is used to initiate the CRS
    crs = init_kzg(alphas[0], crslen)
    #A proof is unnecessary for the first block as alpha is public
    pi = None
    #set the original CRS
    sends[0](0, ["SET_NOWAIT",[pi, crs]])

    bctask = loop.create_task(bc.run())
    if test == 'optimistic':
        checkpoint_options = {
            'mode': 'optimistic',
            'role': 'checkpoint',
            'pl_thresh': None
        }
        passthrough_options = {
            'mode': 'optimistic',
            'role': 'passthrough',
            'pl_thresh': None
        }
        players = [None] * numplayers
        for i in range(1,n):
            #make every third player a "checkpoint" (player that posts progress to the blockchain)
            if i%3 == 0:
                players[i-1] = KZGPlayer(i, alphas[i], sends[i], recvs[i], bcfuncs[i], checkpoint_options)
            else:
                players[i-1] = KZGPlayer(i, alphas[i], sends[i], recvs[i], bcfuncs[i], passthrough_options)
        #send starting message to first player
        sends[0](1, ["INC", None, crs])
    if test == 'robust':
        players = [KZGPlayer(i, alphas[i], sends[i], recvs[i], bcfuncs[i]) for i in range(1,n)]
    playertasks = [loop.create_task(player.run()) for player in players]
    for task in [bctask] + playertasks:
        task.add_done_callback(print_exception_callback)
    #loop.run_forever()
    loop.run_until_complete(playertasks[-1])
